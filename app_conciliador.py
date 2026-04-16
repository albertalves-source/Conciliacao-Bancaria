import streamlit as st
import pandas as pd
import re
import io
import warnings
import requests
import json
import time
import base64
from datetime import datetime, timedelta

# Tenta importar bibliotecas extras de forma segura
try:
    import pdfplumber
except ImportError:
    st.error("Erro: A biblioteca 'pdfplumber' não foi encontrada. Verifique o seu requirements.txt.")

# Configurações de Página
st.set_page_config(page_title="Portal de Conciliação IA - Inteligência Contábil", layout="wide", page_icon="🏦")
warnings.filterwarnings("ignore")

# --- CONFIGURAÇÃO DA IA (GEMINI) ---
api_key = st.secrets.get("GEMINI_API_KEY", "")

def processar_ia_generativa(prompt, image_data=None, mime_type=None):
    if not api_key: return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={api_key}"
    parts = [{"text": prompt}]
    if image_data:
        parts.append({"inlineData": {"mimeType": mime_type, "data": image_data}})
    payload = {"contents": [{"parts": parts}], "generationConfig": {"responseMimeType": "application/json"}}
    try:
        response = requests.post(url, json=payload, timeout=30)
        if response.status_code == 200:
            res_json = response.json()
            content = res_json['candidates'][0]['content']['parts'][0]['text']
            return json.loads(content)
    except: return None
    return None

# --- FUNÇÕES DE APOIO ---
def formatar_moeda(v):
    try:
        val = float(v)
        if val == 0: return "-"
        return f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except: return "-"

def limpar_valor(v):
    if pd.isna(v): return 0.0
    v_str = str(v).replace('R$', '').replace('$', '').replace(' ', '').strip()
    if ',' in v_str and '.' in v_str: v_str = v_str.replace('.', '').replace(',', '.')
    elif ',' in v_str: v_str = v_str.replace(',', '.')
    try: return float(v_str)
    except: return 0.0

def converter_data_dominio(data_obj):
    if pd.isna(data_obj): return None
    try:
        num = float(data_obj)
        if num > 10000: # Data Serial do Excel
            return pd.to_datetime(num, unit='D', origin='1899-12-30').date()
    except: pass
    try: 
        return pd.to_datetime(data_obj, dayfirst=True).date()
    except:
        match = re.search(r'(\d{2}/\d{2}/\d{4})', str(data_obj))
        if match: return datetime.strptime(match.group(1), '%d/%m/%Y').date()
        return None

def normalizar_espacos(texto):
    """Remove espaços duplos e garante formatação perfeita para o Match do Dicionário"""
    if not isinstance(texto, str): return ""
    return " ".join(texto.upper().split())

def formatar_codigo_nome(codigo, nome):
    """Junta o código contábil ao nome. Mostra sempre o código, mesmo se for 9999."""
    cod_str = str(codigo).strip()
    if cod_str.endswith('.0'): cod_str = cod_str[:-2]
    if not cod_str or cod_str in ['nan', 'None', '-', '0']: return f"9999 - {nome}"
    return f"{cod_str} - {nome}"

def extrair_dados_arquivo(file, mapa_bancos, mapa_imp, usar_ia, termos_ignorar):
    transacoes = []
    banco_base = ""
    for b_key in mapa_bancos.keys():
        if b_key in file.name.upper(): banco_base = b_key; break

    # === LÓGICA PARA PDF ===
    if file.name.lower().endswith(".pdf"):
        try:
            with pdfplumber.open(file) as pdf:
                cabecalho = pdf.pages[0].extract_text().upper() if pdf.pages else ""
                if not banco_base:
                    for b_key in mapa_bancos.keys():
                        if b_key in cabecalho: banco_base = b_key; break

                for page in pdf.pages:
                    texto_pagina = page.extract_text()
                    if not texto_pagina: continue
                    
                    linhas_originais = texto_pagina.split('\n')
                    linhas_agrupadas = []
                    linha_temp = ""
                    for l in linhas_originais:
                        if re.search(r'^\s*\d{2}/\d{2}/\d{4}', l):
                            if linha_temp: linhas_agrupadas.append(linha_temp)
                            linha_temp = l
                        else:
                            linha_temp += " " + l
                    if linha_temp: linhas_agrupadas.append(linha_temp)
                    
                    for linha in linhas_agrupadas:
                        linha_upper = linha.upper()
                        if any(x in linha_upper for x in ["SALDO", "RESUMO", "DISPONÍVEL", "DISPONIVEL", "VALOR TOTAL", "TOTAL ACUMULADOR", "SALDO EM"]): continue
                        
                        is_credito = False
                        if any(x in linha_upper for x in ["RECEBID", "DEVOLU", "DESFAZIMENTO", "ESTORNO", "RESSARCIMENTO", "CREDITO", "CRÉDITO", "DEPÓSITO", "DEPOSITO"]):
                            is_credito = True
                        if any(x in linha_upper for x in ["ENVIAD", "PAGAMENTO", "PAGTO", "SAQUE", "COMPRA", "DEBITO", "DÉBITO"]):
                            is_credito = False 
                            
                        if any(t in linha_upper for t in termos_ignorar if t): continue
                        
                        data_match = re.search(r'(\d{2}/\d{2}/\d{4})', linha)
                        valor_match = re.findall(r'(?:R\$\s*)?-?\d{1,3}(?:\.\d{3})*,\d{2}\b', linha)
                        
                        if data_match and valor_match:
                            desc_bruta = linha.replace(data_match.group(1), "")
                            for v_txt in valor_match: desc_bruta = desc_bruta.replace(v_txt, "")
                            
                            # Limpeza Básica
                            nome_limpo = re.sub(r'[A-Z0-9]{8}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{12}', '', desc_bruta.upper())
                            nome_limpo = re.sub(r'\b[A-Z0-9]*\d[A-Z0-9]*\b', '', nome_limpo)
                            for t in ["PIX ENVIADO PARA:", "PIX RECEBIDO PAGADOR:", "TRANSFERÊNCIA ENVIADA PARA:", "TRANSFERÊNCIA RECEBIDA PAGADOR:"]:
                                nome_limpo = nome_limpo.replace(t, '')
                            nome_limpo = normalizar_espacos(nome_limpo)
                            
                            if not nome_limpo: continue

                            cod_found = ""
                            for c in re.findall(r'\b(\d{4})\b', linha):
                                if c in mapa_imp: cod_found = c; break
                            
                            for v_txt in valor_match:
                                val = abs(limpar_valor(v_txt))
                                if val > 0:
                                    transacoes.append({
                                        'Data': [data_match.group(1)], 'Total': val,
                                        'Cod': cod_found, 'Fav': nome_limpo, 
                                        'Banc': banco_base, 'IA': False, 'Arq': file.name,
                                        'Principal': val, 'Multa': 0.0, 'Juros': 0.0,
                                        'Is_Credito': is_credito
                                    })
        except: pass
        
    elif file.name.lower().endswith((".xlsx", ".xls", ".csv")):
        try:
            if file.name.lower().endswith('.csv'):
                try:
                    df_ext = pd.read_csv(file, sep=';', encoding='utf-8-sig')
                    if len(df_ext.columns) < 2:
                        file.seek(0)
                        df_ext = pd.read_csv(file, sep=',', encoding='utf-8-sig')
                except:
                    file.seek(0)
                    df_ext = pd.read_csv(file, engine='python')
            else:
                df_ext = pd.read_excel(file)
            
            for index, row in df_ext.iterrows():
                linha_parts = [str(v) for v in row.values if not pd.isna(v)]
                linha = " ".join(linha_parts).upper()
                
                if any(x in linha for x in ["SALDO", "RESUMO", "DISPONÍVEL", "VALOR TOTAL", "TOTAL ACUMULADOR", "SALDO EM"]): continue
                is_credito = True if any(x in linha for x in ["RECEBID", "DEVOLU", "ESTORNO", "CREDITO", "DEPÓSITO"]) else False
                if any(x in linha for x in ["ENVIAD", "PAGAMENTO", "SAQUE", "DEBITO"]): is_credito = False
                
                if any(t in linha for t in termos_ignorar if t): continue
                
                data_match = re.search(r'(\d{2}/\d{2}/\d{4})', linha)
                valor_match = re.findall(r'(?:R\$\s*)?-?\d{1,3}(?:\.\d{3})*,\d{2}\b', linha)
                
                if data_match and valor_match:
                    desc = linha.replace(data_match.group(1), "")
                    for v_txt in valor_match: desc = desc.replace(v_txt, "")
                    nome_limpo = normalizar_espacos(desc)
                    if not nome_limpo: continue

                    cod_found = next((c for c in re.findall(r'\b(\d{4})\b', linha) if c in mapa_imp), "")
                    
                    for v_txt in valor_match:
                        val = abs(limpar_valor(v_txt))
                        if val > 0:
                            transacoes.append({
                                'Data': [data_match.group(1)], 'Total': val, 'Cod': cod_found, 'Fav': nome_limpo, 
                                'Banc': banco_base, 'IA': False, 'Arq': file.name,
                                'Principal': val, 'Multa': 0.0, 'Juros': 0.0, 'Is_Credito': is_credito
                            })
        except Exception as e:
            st.warning(f"Erro ao ler '{file.name}': {e}")
            
    return transacoes

# --- EXPORTAÇÃO TXT (FORMATO EXATO DO DOMÍNIO CLÁSSICO) ---
def gerar_txt_dominio(df_conciliado, cod_empresa, cnpj_empresa, codigos_bancos_atuais):
    linhas = []
    
    def extrair_conta_limpa(texto):
        m = re.search(r'^(\d+)', str(texto).strip())
        cod = m.group(1) if m else "9999"
        if cod == "0" or cod == "00": return "9999"
        return cod
    
    df_valido = df_conciliado[df_conciliado['Valor Total'].apply(limpar_valor) > 0].copy()
    if df_valido.empty: return ""
    
    datas_todas = []
    for d in df_valido['Data Excel']:
        if str(d) != '-': datas_todas.append(d)
    for d in df_valido['Data PDF']:
        if str(d) != '-': datas_todas.append(d)
        
    datas_parsed = pd.to_datetime(datas_todas, format='%d/%m/%Y', errors='coerce').dropna()
    if not datas_parsed.empty:
        dt_ini = datas_parsed.min().strftime('%d/%m/%Y')
        dt_fim = datas_parsed.max().strftime('%d/%m/%Y')
    else:
        dt_ini = datetime.now().strftime('%d/%m/%Y')
        dt_fim = dt_ini

    empresa_pad = str(cod_empresa).zfill(7)
    cnpj_pad = re.sub(r'\D', '', str(cnpj_empresa)).zfill(14)
    linha01 = f"01{empresa_pad}{cnpj_pad}{dt_ini}{dt_fim}N0500000117"
    linhas.append(linha01)
    
    seq = 1
    for idx, row in df_valido.iterrows():
        val = limpar_valor(row['Valor Total'])
        if val <= 0: continue
        
        cod_deb = extrair_conta_limpa(row['Débito'])
        cod_cred = extrair_conta_limpa(row['Crédito'])
        
        nota_val = str(row.get('Nota', '-')).strip()
        texto_nota = f" NFS {nota_val}" if nota_val and nota_val != '-' else ""
        status = str(row.get('Status', ''))
        favorecido = str(row['Favorecido']).split(' - ')[-1].strip()
        if not favorecido or favorecido == "-": favorecido = "LANCAMENTO CONTABIL"
        
        # Inteligência PAGTO/RECBTO baseada ESTRITAMENTE na conta contábil (Débito/Crédito)
        if "Domínio" in status:
            prefixo = texto_nota.strip()
            hist_texto = f"{prefixo} - {favorecido.upper()}" if prefixo else favorecido.upper()
        else:
            if cod_deb in codigos_bancos_atuais:
                tipo_hist = "RECBTO" # Banco sendo debitado (Ativo aumenta) = Recebimento
            elif cod_cred in codigos_bancos_atuais:
                tipo_hist = "PAGTO"  # Banco sendo creditado (Ativo diminui) = Pagamento
            else:
                val_entrada = limpar_valor(row.get('Entradas', 0))
                tipo_hist = "RECBTO" if val_entrada > 0 else "PAGTO"
                
            hist_texto = f"{tipo_hist}{texto_nota} - {favorecido.upper()}"
        
        data_lanc = row['Data Excel'] if row['Data Excel'] != '-' else row['Data PDF']
        try:
            data_str = datetime.strptime(str(data_lanc), '%d/%m/%Y').strftime('%d/%m/%Y')
        except:
            data_str = dt_ini
            
        linha02 = f"02{str(seq).zfill(7)}X{data_str}".ljust(150)
        linhas.append(linha02)
        seq += 1
        
        v_str = str(int(round(val * 100))).zfill(14) 
        hist_pad = hist_texto[:250].ljust(250)
        linha03 = f"03{str(seq).zfill(7)}{cod_deb.zfill(7)}{cod_cred.zfill(7)}{v_str}        {hist_pad}0000000"
        linhas.append(linha03)
        seq += 1
        
    linhas.append("9" * 100)
    return "\r\n".join(linhas) + "\r\n"

# ==========================================
# 🧠 BANCO DE DADOS INTEGRADO (CÓDIGOS REAIS E BLINDADOS)
# ==========================================
BANCO_DE_DADOS_EMPRESAS_INICIAL = {
    "SELECT OPERATIONS S.A.": {
        "codigo_dominio": "324",
        "cnpj": "56.875.122/0001-86",
        "codigo_matriz_filial": "", 
        "impostos": {
            '0561': {'n': 'IRRF A RECOLHER', 'c': '178'}, 
            '2172': {'n': 'COFINS A RECOLHER', 'c': '180'}, 
            '8109': {'n': 'PIS A RECOLHER', 'c': '179'},
            'ISS': {'n': 'ISS A RECOLHER', 'c': '173'},
            'INSS': {'n': 'INSS A RECOLHER', 'c': '191'}
        },
        "bancos": {
            'BRASIL': {'n': 'Banco do Brasil', 'r': '8'}, 
            'PAYBROKERS': {'n': 'Gatway Paybrokers', 'r': '9'},
            'DELFIN': {'n': 'Delfinance MMABET', 'r': '1107'}, 
            'DELFINANCE': {'n': 'Delfinance MMABET', 'r': '1107'},
            'PAPIGAMES': {'n': 'Delfinance Papigames', 'r': '1119'},
            'BETVIP': {'n': 'Delfinance Betvip', 'r': '1122'},
            'GATWAY MMABET': {'n': 'Gatway API MMABET', 'r': '1052'},
            'GATWAY PAPIGAMES': {'n': 'Gatway API PAPIGAMES', 'r': '1084'},
            'GATWAY BETVIP': {'n': 'Gatway API BETVIP', 'r': '1085'}
        },
        "fornecedores": {
            'CONNECTPSP DESENVOLVEDORA': '374',
            'CONNECTPSP': '374',
            'UNIFICAPAY SERVICOS': '1774',
            'UNIFICAPAY': '1774',
            'DOM ASSESSORIA': '1358',
            'PAGLIVRE SOLUCOES': '1786',
            'PAGLIVRE': '1786',
            'SELECT OPERATIONS LTDA': '5',
            'SELECT OPERATIONS': '1052',
            'ACG INSTITUICAO': '536',
            'ISS': '173',
            'AVANT EXPANSAO': '1188',
            'JOAO MARCOS': '1577',
            'GABRIELLA': '1822',
            'DUCAMPELO PARTICIPACOES': '1837',
            'DUCAMPELO': '1837',
            'LEAME SOMA': '1784',
            'BRAFIN SOLUCOES': '1412',
            'REDESPARK TECNOLOGIA': '1676',
            'SARA': '1853',
            'EDUARDO': '1418',
            'ARLEQUIM': '1516',
            'INTERNATIONAL BET': '1840',
            'DIEGO': '1314',
            'SIDNEY': '1854',
            'JOAO WESLEY': '1841',
            'DOMINIQUE LIMA': '5',
            'VALENTIM SOLUCOES': '1186',
            'PYERRE SAYMON': '1187',
            'ERICA MAXIMO': '1838',
            'BRASIL CONSULTORIA': '1634',
            'LEGITIMUZ': '1183',
            'JUST NOW': '1884',
            'MAILINBOX COMUNICACOES': '1673',
            'PUBLICIDADE PROMOCAO': '1647',
            'GUILHERME ESTEVES': '1640',
            'BUZZCRAFT DIGITAL': '1409',
            'MARCELO': '1518',
            'MATHEUS CASTRO': '1887',
            'CACTUS TECNOLOGIA': '1880',
            'OBVIO': '1604',
            'YGOR EDUARDO': '1804',
            'SINGLE SOFTWARE': '1449',
            'GBM INFO': '1185',
            'COMERCIO SERVICO': '5',
            'BETCONNECT INTERNET': '1717',
            'FOMENTO PUBLICIDADE': '1637',
            'JHONATHAN': '1883',
            'SENDWORK SERVICOS': '1530',
            'EZEQUIEL WANDERLEY': '1839',
            'MOVE COMPANY': '1675',
            'GAMIFY TECH': '1503',
            'LEANDRO PEREIRA': '1885',
            'GEG SOLUCOES': '1881',
            'SELECT BET': '1889',
            'C13 ENTRETENIMENTO': '1850',
            'ANDRE': '1775',
            '573 470': '1849',
            'TRAFEGAR MIDIAS': '1333',
            'GUSTAVO': '1882',
            'LUCAS PAULO': '1886',
            'VITOR': '1890',
            'MMK NEGOCIOS': '1888',
            'AFFILIATESDIGITAL LTDA': '1879',
            'VORTIX CORE': '1722',
            'FORNECEDORES DIVERSOS': '1126',
            'DECOLA OPERATIONS N.V.': '1083',
            'KAYQUE DA SILVA LOPES': '1402',
            'SOUTO, CORREA SOCIEDADE DE ADVOGADOS': '1403',
            'PAPV SERVIÇOS LTDA': '1138',
            'SEND SPEED PRODUTOS E SERVIÇOS LTDA': '1139',
            'JP BALÕES': '1140',
            'SISTEMA DE SEGURANÇA KYC': '1141',
            'AGENCIA MARKETING ESTRELA': '1142',
            'CAMAROTE SHOWS E EVENTOS LTDA': '1143',
            'TV SBT CANAL 4 SAO PAULO S/A': '1144',
            'SMARTICO CASA DE CAMBIO': '1145',
            'CAUCAIA SPORT CLUB': '1146',
            'DR DANIEL MORAIS': '1147',
            'EQUIPE DE MARKETING - WELTON ALVES': '1151',
            'EQUIPE DE MARKETING - NEEMIAS JUDSON DE OLIVEIRA': '1152',
            'X1 AO VIVO CAMPEONATOS LTDA': '1160',
            'CONTRATO MENSAL - RAY VELOSO': '1161',
            'CONTRATO MENSAL - JOSE FELIX DO NASCIMENTO': '1162',
            'INFLUENCIADOR - NATHANZINHO': '1163',
            'ASSESSORIA CONTABIL - ANDERSON VALENTIM': '1164',
            'MARKETING - MALU': '1165',
            'RAFAEL CUNHA': '1166',
            'INFLUENCIADOR JOSE EDVALDO ALVES DE OLIVEIRA': '1167',
            'INFLUENCIADOR - FRALDIANE RODRIGUES DA SILVA': '1168',
            'INFLUENCIADOR - MARIANA DE OLIVEIRA VELOSO': '1169',
            'INFLUENCIADOR - LUZIA RAMILA VIEIRA DE SOUZA': '1170',
            'PRESTAÇÃO SERVIÇO JURIDICO': '1171',
            'TRÁFEGO - PAGCORP': '1172',
            'ALUGUEL FILIAL - RJ': '1173',
            'SUPORTE NO CRM DE RELACIONAMENTO': '1174',
            'ACORDO JUSTIÇA - BETVIP': '1175',
            'EDIVANIO DA PAZ': '1558',
            'BETVIP - REUNIAO': '1177',
            'BETVIP - WATILA': '1178',
            'MUSICA VIVA LTDA': '1221',
            'TELEATENDIMENTO (BRASGAMING)': '1222',
            'REY VAQUEIRO': '1224',
            'IMPUSEMAX MARKETING': '1225',
            'YARA TCHE': '1226',
            'TATY GIRL': '1227',
            'MORAES NEVES ADVOGADOS': '1228',
            'SPELL AGENCIA DE DESENVOLVIMENTO E SOLUÇÕES DE SOFTWARE LTDA': '1229',
            'TIRULLIPA PRODUCOES LTDA': '1230',
            'YASMIN NICOLI ALVES DE CARVALHO': '1232',
            'JEAN DOS SANTOS MOREIRA': '1233',
            'X7 ASSESSORIA': '1235',
            'JOSE EDSON VIEIRA DOS SANTOS': '1236',
            'DR SERVIÇOS LTDA': '1243',
            'TALKING & GAMING LTDA': '1261',
            'JOSE FRANCISCO DA SILVA JUNIOR': '1262',
            'LUCAS DANTAS PONTES': '1263',
            'JOAO SUCUPIRA': '1305',
            'WILTON RAMOS BITTENCOURT': '1265',
            'JUNYELLE MATOS ROCHA': '1306',
            'TARCISIO ARAUJO PEREIRA FILHO MKD': '1267',
            'LARYSSA CARLA FREITAS MATOS': '1307',
            'B. IMAGEM CENOGRAFIA E EVENTOS LTDA': '1269',
            'AIDA TRAFFIC': '1308',
            'PEIXE DA LAMA SERVICOS DIGITAIS LTDA': '1271',
            'NELSON WILIANS & ADVOGADOS ASSOCIADOS': '1309',
            'ERICK JOSE DE ARAUJO CASADO': '1273',
            'NLN PROMOCOES LTDA': '1275',
            'ACG ADMINISTRADORA': '1310',
            'TATO COMUNICACAO VISUAL LTDA': '1277',
            'ARTUR EUDES ARAUJO BELO': '1374',
            'MARCO FABIANO PEREIRA FRANCO DA COSTA': '1279',
            'ANIMA BRINDES INDUSTRIA DE COMERCIO': '1454',
            'CBLABEL FABRICACAO E COMERCIO DE PRODUTOS PLASTICOS LTDA': '1281',
            'CLINT HUB SERVICOS DIGITAIS LTDA': '1512',
            'LEANDRO PEDRO CANTON - CANTON IMPRESSOES': '1283',
            'SERTAO PRINT COMERCIO E SERVICO LTDA': '1553',
            'TICIANA SALES DE OLIVEIRA': '1285',
            'HOTEL OASIS DE CAJAZEIRAS LTDA': '1554',
            'ENJOY MUSIC SOLUCOES MUSICAIS LTDA': '1287',
            'TROODON PARK HOTEL LTDA': '1555',
            'RAFAELA DE SOUSA VIEIRA': '1556',
            'ANDRESON COSTA DANTAS MOREIRA - SDR DIGITAL': '1290',
            'AGROPECUARIA GRATIDAO LTDA': '1291',
            'IUGU INSTITUICAO DE PAGAMENTO S.A.': '1292',
            'PYERRE SAYMON DE MELO SILVA SOCIEDADE INDIVIDUAL DE ADVOCACIA': '1293',
            'ALICE A.C DA SILVA PUBLICIDADE': '1294',
            'VANGUARDA - COMERCIO E SERVICO DE BALOES PUBLICITARIOS LTDA': '1295',
            'PORCINO FERNANDES DA CONSTA SEGUNDO': '1296',
            'FX PARTICIPACOES S/A': '1297',
            'TIROSDECANTO MARKETING DIGITAL LTDA': '1298',
            'ATIVE PROMOCOES E EVENTOS LTDA': '1299',
            'LAERCIO RODRIGUES DA CRUZ': '1300',
            'CAIO VITOR LIMA MODESTO DE QUEIROZ': '1301',
            'GABRIEL ALMEIDA RADICA DA SILVA': '1302',
            'DIEGO C. DOS SANTOS COMERCIO': '1303',
            'RAFAEL DIEGO KREHNKE GONCALVES': '1304',
            'DANTAS CM & AM LTDA': '1313',
            'TAISSUKE LOCACOES LTDA': '1557',
            'JOYO TECNOLOGIA BRASIL LTDA.': '1315',
            'SELBR SERVICE LTDA': '1316',
            'MOZART RODRIGUES CASTELLO SOCIEDADE INDIVIDUAL DE ADVOCACIA': '1317',
            'AFILIAPIX SOLUCOES EM MARKETING E TECNOLOGIA LTDA': '1318',
            'CHECKMATE MARKETING DIGITAL LTDA': '1319',
            'STEPHANY DOS SANTOS REIS': '1320',
            'BRAFIN SOLUCOES, INTERMEDIACAO E PAGAMENTOS LTDA': '1412',
            'CAMPOS EMPREENDIMENTOS E TECNOLOGIA LTDA': '1322',
            'DOM - ASSESSORIA ESPORTIVA E EMPRESARIAL LTDA': '1323',
            'FLUE AGENCIA DIGITAL LTDA': '1324',
            'GABRIELLY FERNANDA BORGES DA LUZ': '1325',
            'GABRIEL ADEMAR CRAVEIRO DA CUNHA': '1326',
            'JOAO VINICIUS DE OLIVEIRA': '1327',
            'LEANDRO DA SILVA DOS SANTOS': '1328',
            'LUIZ FELLIPE DO NASCIMENTO RAMOS': '1329',
            'MURILO DA SILVA PITA': '1330',
            'VITOR MAGNO F SALES PUBLICIDADE': '1331',
            'PVT 1 EDITORA LTDA': '1332',
            'TRAFEGAR MIDIAS LTDA': '1333',
            'VIRTUALCOB PROCESSAMENTO DE DADOS LTDA': '1334',
            'JOÃO VICTOR GOMES COUTINHO': '1559',
            'ROMUALDO DE FARIAS SILVA FILHO': '1336',
            'MATEO SCUDELER SOCIEDADE INDIVIDUAL DE ADVOCACIA': '1560',
            'AJBO CONSULTORIA': '1620',
            'OSANDI GADELHA DE SOUSA SILVA': '1562',
            'ROBERTO LUIZ': '1568',
            'ANDERSON DA SILVA VALENTIM': '1342',
            'SAMORE TECNOLOGIA': '1569',
            'CONTINENTAL MIDIA SERVICOS E NEGOCIOS LTDA': '1344',
            'CARIOCA BEER COMÉRCIO E DISTRIBUIDORA DE BEBIDAS LTDA': '1379',
            'ABL CONSULTORIA E SERVICOS LTDA': '1378',
            'DANYELLE LIMA DOS SANTOS DE FARIAS': '1380',
            'LOC ESTUDIOS E EQUIPAMENTOS LTDA': '1381',
            'JOSE AILTON GOMES': '1383',
            'FERNANDA CAROLINE LEIROZ': '1384',
            'FRANCISCO ANTONIO DE OLIVEIRA NETO': '1389',
            'PAIOL COMERCIO E COMUNICACAO VISUAL LTDA': '1390',
            'GAMIFY TECH BRASIL LTDA': '1391',
            'CARRETA DO MAMUTE SERVICOS E LOCACOES LTDA': '1392',
            'NETTRAVELS AGENCIA DE VIAGENS E TURISMO LTDA': '1386',
            'SGSA ALIMENTACAO PARA EVENTOS EIRELI': '1387',
            'EARLYBIRD BRASIL LTDA': '1388',
            'ISIS DE QUEIROZ PEREIRA OLIVEIRA': '1394',
            'B.PERSONALIZED LTDA': '1382',
            'EXTERMINE DEDETIZADORA E SERVIÇOS LTDA - ME': '1393',
            'OFICINA DAS MARCAS': '1601',
            'CARLOS EDUARDO PALU': '1400',
            'AYLA PARTICIPAÇÕES': '1570',
            'CODING DESENVOLVIMENTO': '1571',
            'SINGLEBYTE DESENVOLVIMENTO': '1572',
            'HOTEL LUZEIROS': '1573',
            'DIGIMAX MARKETING': '1621',
            'FISH PUBLICIDADE': '1622',
            'FORMULA IMPORTAÇÕES': '1623',
            'HOTEL ITABAIANA': '1624',
            'LOJA MARACAJA': '1625',
            'MANRRATAN PRODUTORA': '1626',
            'RM MARKETING PUBLICIDADE': '1627',
            'MBA ASSESSORIA': '1628',
            'GROW UP': '1629',
            'GRACIELLY WILIANE': '1630',
            'LUCAS MYCHEL': '1632',
            'KOKOPELLI SOLUCOES TECNOLOGIAS LTDA': '1652',
            'GUSTAVO SILVERIO ALMEIDA': '1653',
            'K C EVENTOS E CONGRESSOS LTDA': '1654',
            'GOOGLE BRASIL INTERNET LTDA': '1655',
            'MIDIA PRODUÇÕES LTDA': '1656',
            'ALYNE PALMEIRA': '1690',
            'CENTRALIZE STUDIO': '1691',
            'CRMD PRODUCOES': '1692',
            'GRAZIELE FERRAZ FRANCO DA COSTA': '1693',
            'L GERADORES LOCACOES E SERVICOS LTDA': '1694',
            'HUGO JOSE ALVES LACERDA': '1695',
            'INFLUENCER ACADEMMY PUBLICIDADE': '1696',
            'JAMMING JAGUAR': '1697',
            'LEAG DIGITAL': '1698',
            'LED SPORTS': '1699',
            'LUYD GUSTAVO THEODULINO DE FARIAS': '1700',
            'NOVITTA RENT A CAR': '1701',
            'PAULO RICARDO ESCOSSIO DE FREITAS FILHO': '1702',
            'JF PRODUCOES': '1703',
            'RODRIGUES NETO ADVOGADOS ASSOCIADOS': '1704',
            'SOLUÇÕES IND TECK NATHA LTDA': '1705',
            'BRASTUR AGENCIA': '1723',
            'CASSIANO SILVA': '1724',
            'COMPUCARD INDUSTRIA': '1725',
            'AZO DIGITAL': '1726',
            'ATIVA TRAVEL': '1727',
            'ATIVA SOLUCOES': '1728',
            'ANTONIO MIGUEL': '1729',
            'ANGELO FERNANDES': '1730',
            'ANDERSON OLIVEIRA': '1731',
            'ACH PRODUCOES': '1732',
            'ABERLANDIA KELLY DA SILVA NASCIMENTO': '1733',
            'DIGITAL HUB': '1734',
            'EGYPTUS SERVICOS': '1735',
            'EQUIPA PROTECAO': '1736',
            'ESL PRODUCOES': '1737',
            'TALITA MEL': '1738',
            'TECNO INDUSTRIA': '1739',
            'VEDINALDO RAMOS': '1740',
            'VOENATAL CONSULTORIA': '1741',
            'ZEMOTION LTDA': '1742',
            'CONECTA IGAMING': '1743',
            'FARIA PRODUCOES': '1744',
            'FMX CERTIFICAÇÃO': '1745',
            'HOTEL VALE DO JATOBA LTDA': '1746',
            'JERONIMO RIBEIRO': '1747',
            'JOAO FELIPE': '1748',
            'KAIK PRODUCOES': '1749',
            'LUCAS XIMENES': '1750',
            'MARTINIANO SILVA': '1751',
            'MATHEUS CARLO': '1752',
            'MAURICIO SERAFIM': '1753',
            'MEDIA EVOLUTION': '1754',
            'MIDIAS PRODUCOES': '1755',
            'MJR PRODUCOES': '1756',
            'NATTAN PRODUCOES': '1757',
            'PRISMA DATAVISION': '1758',
            'RAWLISSON MENESES': '1759',
            'SIGNATURE CONSULTORIA': '1760',
            'GRUPO CASAS BAHIA': '1761',
            'AMANDA MAYARA TEIXEIRA DA SILVA': '1762',
            'JOSE GALDINO ALVES': '1763',
            'ROSIMAR DOS SANTOS DIAS': '1764',
            'MARIA VITORIA CAVALCANTI DA SILVA': '1765',
            'SARA MATOS MELO': '1766',
            'SHERIDA DE SOUSA MOREIRA': '1767',
            'WALDIR MADUREIRA': '1768',
            'TREINAE MODA FITNESS COMERCIO E SERVICOS LTDA': '1769',
            'WSM SERVICOS LTDA': '1770',
            'NL PRODUCOES ARTISTICAS': '1771',
            'JOAO BATISTA DE LIMA': '1772',
            'ARTHUR AGNELO SOARES DELLA LIBERA': '1787',
            'GABRIEL ALEXANDRE FEITOSA JUNIOR': '1788',
            'ANDRESSA NATHYLA RAULINO OLIVEIRA': '1816',
            'MIKAELLA OLIVEIRA ALVES': '1790',
            'MUNDO DAS FARDAS': '1791',
            'PEDRO FELINDO': '1792',
            'RAFAEL ALVES DE JESUS': '1793',
            'RHAYAN MUSA RABAH': '1794',
            'PG SHOWS E ENTRETENIMENTO': '1795',
            'TARGINO TUR': '1796',
            'MARINA SOUSA DO NASCIMENTO': '1797',
            'WILLTEMBERG RODRIGUES': '1798',
            'YTA FEST LOCACOES LTDA': '1799',
            'DOMINIQUE LIMA DE APOCALYPSES PEREIRA': '1803',
            'ANTONIO ALISSON': '1817',
            'ARTE FOGOS': '1818',
            'CARLOS DIEGO': '1819',
            'CLEBERSON RENATO': '1820',
            'CONVERTAX MARKETING': '1821',
            'VITOR MANOEL DE SOUZA': '1631',
            'GABRIELLA CARNEIRO ALMEIDA': '1822',
            'JORGE BARROS DE OLIVEIRA': '1823',
            'LOPES TRANSPORTES': '1824',
            'MARCIO CEZAR': '1825',
            'MARCO TULIO': '1826',
            'NATHALIA DA SILVA MARTINS': '1827',
            'NEXUS TEC': '1828',
            'PALMER EMPREENDIMENTOS': '1829',
            'PAULINHA RAVETT': '1830',
            'PRAXEDES': '1831',
            'SHIPP ASSESSORIA': '1832',
            'SILVANA AQUINO': '1833',
            'SUPORT CONSULTORIA': '1834',
            'THIAGO MARQUES GUEDES FARIAS': '1835',
            'VICTOR COSTA DA SILVA': '1836',
            'SINGLE SOFTWARE SOLUCOES TECNOLOGIAS LTDA': '1844',
            'CUTMIDIA COMUNICAÇÃO': '1845',
            'FACEBOOK SERVICOES ONLINE DO BRASIL LTDA': '1846',
            'FERNANDO RICARDO': '1847',
            'RICARDO AUGUSTO': '1848',
            'FRANCISCO CLEONILSON RAMOS COSTA': '1857',
            'ANDRIEL ALEXANDRE DE OLIVEIRA': '1858',
            'YURI DOS SANTOS LACERDA': '1859',
            'LOURIVAL MATIAS JUNIOR': '1860',
            'ANDREZA SOUSA DA MOTA MADUREIRA': '1861',
            'MAGAZINE LUIZA': '1862',
            'ART IMPRESSAO': '1869',
            'BLCK BRASIL': '1870',
            'GLOBAL DISTRIBUIDORA': '1871',
            'IMPRIMA JUAZEIRO': '1872',
            'LUIZ PHILIPP DA SILVA GOMES': '1874',
            'MOVENORD MOVEIS': '1875',
            'POWERED BRASIL': '1876',
            'RAYANE FERNANDES SANTANA': '1877',
            'ROMARIO RODRIGUES': '1878'
        }
    },
    "PIXBET SOLUCOES TECNOLOGICAS LTDA": {
        "codigo_dominio": "0000",
        "cnpj": "00.000.000/0000-00",
        "codigo_matriz_filial": "2",
        "impostos": {
            '0561': {'n': 'IRRF s/ Salários', 'c': '9999'}, 
            '2172': {'n': 'COFINS', 'c': '428'}, 
            '8109': {'n': 'PIS', 'c': '429'},
            'ISS': {'n': 'ISS', 'c': '427'}
        },
        "bancos": {
            'BRASIL': {'n': 'Banco do Brasil', 'r': '8'},
            'FOXBIT': {'n': 'Foxbit Invest Custódia', 'r': '1618'},
            'PAGCORP': {'n': 'Cartões Pagcorp Flabet', 'r': '1845'},
            'ZERO': {'n': 'Banco Zero Bet da Sorte', 'r': '1857'},
            'DELFIN PIXBET': {'n': 'Delfinance Proprietaria - Pixbet', 'r': '1110'},
            'DELFIN FLABET': {'n': 'Delfinance Proprietaria - Flabet', 'r': '1111'},
            'DELFIN BET DA SORTE': {'n': 'Delfinance Proprietaria - Bet da Sorte', 'r': '1112'}
        },
        "fornecedores": {
            "Z3 PROPAGANDA LTDA": "1254",
            "STAMPA OUTDOOR LTDA": "1260",
            "BLACK BOX DIGITAL LTDA": "1261",
            "EXPERT DIGITAL": "1262",
            "ANA&ROSA EVENTOS ARTISTICOS LTDA": "1263",
            "PONTO P - TECNOLOGIA E PAGAMENTOS LTDA": "1264",
            "HI COMUNICA MARKETING LTDA": "1265",
            "GS - TRAFEGO ORGANICO LTDA": "1266",
            "GAMIFY TECH BRASIL LTDA": "1684",
            "ADMASTERS SOLUCOES DE MARKETING DIGITAL LTDA": "1268",
            "PEDALAR LOCACAO DE EQUIPAMENTOS DE LAZER LTDA": "1272",
            "EUDSON HENRIQUE DE FREITAS": "1273",
            "DANIEL FORTUNE DIGITAL MARKETING LTDA": "1274",
            "FABIO C. SIMÕES - SIMÕES DIVULGAÇÕES LTDA": "1275",
            "ACG ADM. DE CARTÕES": "1276",
            "ASSOCIAÇÃO CENTENARIO DO SANTA CRUZ": "1277",
            "SHORT CODE AUTOMACAO DE SERVICOS LTDA": "1278",
            "PIX GAMING DIGITAL MARKETING LTDA": "1566",
            "MP JORNALISMO E PROPAGANDA LTDA": "1280",
            "DESENVOLVIMENTO LTDA": "1281",
            "LOMA AGENCIA E MARKETING LTDA - PLAY ONLINE": "1282",
            "SHOWS PRODUÇÃO": "1283",
            "BHS BRINDES": "1284",
            "TRAFEGAR MIDIAS": "1285",
            "BEBERIBE MIDIA E COMUNICACAO LTDA": "1286",
            "FIRSTSTEP CONSULTORIA": "1287",
            "NEXUS TELECOM": "1288",
            "ALL SPACE": "1289",
            "BR CONSULTORIA ESPORTIVA LTDA": "1290",
            "ANNE STEPHANINE PEREIRA DE AQUINO": "1291",
            "GYNO DANIEL BEZERRA SILVA": "1292",
            "1001 SERVIÇOS DIGITAIS EIRELI": "1293",
            "ONIMIDIA SERVICOS DE MARKETING LTDA": "1294",
            "MOVE COMPANY LTDA": "1295",
            "MC4 PROMO MARKETING DIRETO LTDA": "1296",
            "PRISCILA DE ARAUJO DORNELAS CAMARA": "1297",
            "FR SOLUCOES EM MARKETING LTDA": "1298",
            "TGF DIGITAL MARKETING LTDA": "1299",
            "CARLOS VINICIUS SANTOS DE LIMA": "1300",
            "JUARES PINTO DE ALENCAR": "1301",
            "GABRIEL RIBEIRO CHAVES": "1302",
            "CARLOS AUGUSTO AFONSO MOREIRA": "1303",
            "SUELEN KARINE DA SILVA ROCHA": "1304",
            "LUCAS MATHEUS MORAIS DE LIMA": "1305",
            "GOMES CIA": "1306",
            "ICARO FERNANDO DOS SANTOS PEREIRA": "1307",
            "MARCO ANTONIO DE SOUZA BARBOSA": "1308",
            "MARCOS VILLAS BOAS FRANCA": "1310",
            "ARTUR TORRES DE MOURA FILHO": "1311",
            "IMPERIO VERDE MARKETING DIGITAL LTDA": "1312",
            "RADIO TRANSAMERICA DE RECIFE LTDA": "1313",
            "VILLARIM MARQUES PUBLICIDADE LTDA": "1314",
            "DNE - MIDIA LTDA": "1315",
            "RZK DIGITAL MEDIA COMERCIALIZAÇÃO DE MIDIA LTDA": "1316",
            "GABRIEL DA SILVA MARQUES": "1317",
            "DBONE COMERCIO DE VESTUARIOS E ACESSORIOS LTDA": "1318",
            "JULIO CESAR ALVES BRAGA": "1319",
            "ANDRE LUIZ ALVES RIBEIRO": "1320",
            "MARLLON LEVY OLIVEIRA SANTOS": "1321",
            "HERBERT PERFEITO TRAMONTINI": "1322",
            "GABRIELA LOHANA DE MELO PUBLICIDADE": "1323",
            "JOAO LUCAS BARROS DE ALMEIDA": "1324",
            "SADI & MORISHITA ADVOGADOS ASSOCIADOS": "1326",
            "MATHEUS VICTOR DE OLIVEIRA SANTOS": "1327",
            "FACIL TRANSFER COMERCIO DE CAMISAS LTDA": "1347",
            "PG NEGOCIOS DIGITAIS": "1348",
            "BIRO BRASIL SERVIÇOS DE IMPRESSÃO": "1349",
            "LOPES EMPREENDIMENTOS DIGITAL": "1351",
            "LEGITIMUZ TECNOLOGIA": "1352",
            "RICK BANDEIRA PRODUÇÕES": "1353",
            "LETICIA LUIZA MENDES": "1354",
            "CD PUBLICIDADE E EVENTOS": "1355",
            "ARRAIAL - ACADEMIA DO GOL FUTEBOL SOCIETY LTDA ME": "1356",
            "DIOMAR TADEU DANTAS DE FARIAS - BRASGAMING": "1357",
            "MABRE MARKETING LTDA": "1358",
            "J.Q SERVIÇOS E CONSULTORIA LTDA": "1359",
            "CHAILLINE AZEVEDO ALVES": "1360",
            "DANYELLA DO NASCIMENTO ARCANJO": "1361",
            "CENOCARVA LTDA": "1362",
            "JW INFLUENDER LTDA": "1363",
            "MARCOS DANIEL VALE": "1364",
            "SMITH RYAJ COSTA DE SOUZA": "1365",
            "ANDERSON DA SILVA VALENTIM": "1366",
            "GABRIEL HENRIQUE GOMES DA SILVA": "1367",
            "VRL AGENCIAMENTOS DE VIAGENS LTDA": "1368",
            "IMPERIO DOS BALOES": "1369",
            "OLIVEIRA PAZ LTDA": "1370",
            "MASTER DIGITAL COMERCIO DE PRODUTOS ELETRONICOS": "1373",
            "LDA E ESPORTS LTDA": "1376",
            "RODRIGO IKE ENTERTAINMENT LTDA": "1379",
            "DC DIGITAL LTDA": "1383",
            "ELLEN JULIANA DO CARMO SALES COSTA": "1385",
            "WDN ESPORTES LTDA": "1388",
            "JENYFER SCHIMANSKI DA CRUZ": "1390",
            "CLAUDIA COSTA FARIAS": "1392",
            "SIMONE MACHADO PINTO ELLYAN": "1393",
            "CARIJO COMUNICAÇÃO LTDA": "1395",
            "IVAH MARKETING E GAMING LTDA - IURY ANDREI": "1397",
            "CORREA CORREA COMUNICAÇÃO LTDA": "1400",
            "THIAGO WILSON DA SILVEIRA": "1402",
            "MP SERVIÇOS GRÁFICOS E PUBLICITÁRIOS": "1408",
            "HUMBERTO CALABRIA FILHO": "1409",
            "WILLYAN DE FRANCA SANTANA DOS SANTOS": "1410",
            "N CONTEUDO DE MARKETING LTDA": "1415",
            "WDT GRÁFICA E EDITORIA EIRELI": "1423",
            "PEDRO HENRIQUE DE MATOS DIAS CHIANCA": "1424",
            "RALI NEGOCIOS DIGITAIS LTDA": "1614",
            "I C DE LIMA NEGOCIOS DIGITAIS LTDA": "1615",
            "GIULIA CIANDRINI DE MENDONCA CAMARA ARAUJO": "1616",
            "FACEBOOK SERVICOS ONLINE DO BRASIL LTDA": "1681",
            "ADRIANO DA CONCEICAO SOUZA": "1469",
            "RECIFE TRACKER LTDA": "1620",
            "GOOGLE BRASIL INTERNET LTDA": "1682",
            "MARLON COUTO DE LIMA": "1472",
            "LAURO MARCELO GUEDES MONTEIRO": "1622",
            "VALERIA DE FARIA SILVA FERREIRA": "1623",
            "CARLOS HENRIQUE ANDRADE DA SILVA": "1624",
            "CARLOS APARECIDO TEODORA DE CARVALHO": "1625",
            "VANESSA ALCANTARA TRAMONTINI": "1477",
            "TIROSDECANTO MARKETING DIGITAL LTDA": "1626",
            "RENATO DE JESUS BARBOSA LIMA": "1627",
            "BB AFFILIATION AGENCIA DE PUBLICIDADE LTDA": "1480",
            "IDEA LOCACAO DE ESTRUTURAS E ILUMINACAO LTDA": "1481",
            "SARA BRANDAO SANTOS": "1628",
            "RONALDO GUEDES DA SILVA": "1685",
            "GABRIEL BORGES SOARES DA SILVA": "1630",
            "DNC GESTAO E ANALISE DE DADOS LTDA": "1631",
            "MARCELO AUGUSTO DA SILVA": "1632",
            "IAN GUIMARAES HASTENREITER": "1633",
            "DAVI DE F RODRIGUES": "1634",
            "BRUNO ANDRE MORAIS DE LIMA": "1686",
            "PROMOBEM ESPIRITO SANTO LTDA": "1636",
            "MIRELLY DOS SANTOS FERNANDES": "1491",
            "ANDRE LUIZ ALVES CORREIA": "1492",
            "DUBLATEXTIL FABRICACAO DE TECIDOS LTDA": "1493",
            "PROMOBEM SAO PAULO LTDA": "1637",
            "PROMOBEM PERNAMBUCO LTDA": "1638",
            "MAXIMILIANO MENEZES DE MELO": "1496",
            "PROMOBEM GOIAS LTDA": "1639",
            "MARLIO AVILA DE C NEVES JUNIOR": "1498",
            "PROMOBEM PARA LTDA": "1640",
            "ARES REPRESENTAÇÕES COMERCIAIS LTDA": "1500",
            "SERGIO HACKER CORTE REAL": "1502",
            "SILVANA F. DE LIMA FLORES - ME": "1505",
            "PROMOBEM BAHIA LTDA": "1641",
            "PROMOBEM AMAZONAS LTDA": "1642",
            "PEDRO HENRIQUE SANCHES FERREIRA": "1508",
            "RONIVAL SALES PEREIRA": "1509",
            "PROMOBEM ALAGOAS LTDA": "1643",
            "OTAVIO NASCIMENTO DE SOUZA": "1511",
            "NILSON JOSE CARMO DA SILVA FILHO LTDA": "1644",
            "MARCELLA WOILLE INOJOSA GALINDO SILVA": "1514",
            "JOHN VICTOR BAI FRANCISCO": "1515",
            "HOTBIZ LTDA": "1516",
            "COMMINITY DIGITAL LTDA": "1517",
            "FOMENTO PUBLICIDADE INDUSTRIES BRASIL LTDA": "1518",
            "SR DRIVE EXPERIENCE LTDA": "1519",
            "JOAO VICTOR AMORIM FREITAS": "1645",
            "QXUTE LTDA": "1521",
            "RAFAEL SILVERIO LEAL": "1523",
            "RADIO JC FM LTDA": "1646",
            "APX ENGAGE - DIGITAL SOLUTIONS LTDA": "1525",
            "RADIO SOCIEDADE DA BAHIA SOCIEDADE ANONIMA": "1647",
            "MAILINBOX COMUNICACOES LTDA": "1527",
            "HM TECH LTDA": "1528",
            "BRUNO AUGUSTO MACIEL ZAMBONI": "1687",
            "THIAGO WILLIAMS BEZERRA ZILLINGER": "1532",
            "TORRES GADELHA SOCIED. IND. DE ADVOCACIA": "1533",
            "MILLENNIUM PNEUS LTDA": "1534",
            "PB DIGITAL LTDA": "1649",
            "MC_PUBLICIDADE E MARKETING LTDA": "1536",
            "RAFAEL DE BARROS LIRA VASCONCELOS": "1650",
            "PROCONECT LTDA": "1538",
            "LUIZ ROCHA LELES JUNIOR": "1651",
            "P L S A MARKETING DIGITAL LTDA": "1652",
            "WAKE UP LTDA": "1656",
            "SHAOLIN PRODUCOES LTDA": "1542",
            "C. E. DA SILVA LTDA": "1657",
            "DEJO DO BRASIL LTDA": "1544",
            "SEU TITO BOTECO LTDA": "1688",
            "ADVICE MULTIMIDIA SERVICOS E LOCACOES LTDA": "1658",
            "AGENCIA LUCK VIAGENS E TURISMO LTDA": "1547",
            "GRA VIOLA PRODUCOES ARTISTICAS LTDA": "1659",
            "DCCONVERSION SERVICOS DIGITAIS LTDA": "1660",
            "BETTER COLLECTIVE BRASIL LTDA": "1689",
            "MOVEUP MEDIA BRAZIL LTDA": "1662",
            "IVY PRODUCOES ARTISTICAS LTDA": "1663",
            "SINGLE SOFTWARE SOLUCOES TECNOLOGIAS LTDA": "1666",
            "SINOSSERRA PROMOTORA DE VENDAS E SERVICOS FINANCEIROS LTDA": "1690",
            "BCMV COMUNICACAO E MARKETING LTDA": "1691",
            "AILTON RICARDO MOREIRA GALDINO ME": "1692",
            "RAFAELA OLIVEIRA CHRIZOSTOMO": "1693",
            "GODAN30 LTDA": "1694",
            "SACCA PUBLICIDADE E MERCHANDISING LTDA": "1695",
            "EVERTON LUIS DA SILVA XAVIER": "1696",
            "HC TURISMO LTDA": "1697",
            "ERIVALDO DE ANDRADE FERREIRA": "1698",
            "LET'S TURISMO LTDA": "1699",
            "LUCAS DO ESPIRITO SANTOS SOUZA": "1700",
            "CAROLINA R DE A CALABRIA EVENTOS LTDA": "1701",
            "BENIGNO DA COSTA LEAO JUNIOR": "1702",
            "MUNICIPIO DE CABEDELO": "1725",
            "MAINSTREAM CONSULTORIA DE ESPORTES ELETRONICOS LTDA": "1731",
            "MALT SERVICOS DE LIMPEZA LTDA": "1732",
            "MOURA VIDROS LTDA": "1733",
            "NORDESTE BRINDES E VARIEDADES LTDA": "1734",
            "GBM INFO LTDA": "1735",
            "IMAS BRASIL ARTIGOS RECREATIVOS LTDA": "1736",
            "ANDERSON BITENCOURT DE JESUS": "1737",
            "BAROJO COMERCIO E SERVICOS LTDA": "1791",
            "L M LINK SOLUCOES EM TECNOLOGIA LTDA": "1740",
            "BENU MEDIA LTDA": "1741",
            "CS CONSTRUCOES LTDA": "1742",
            "ANIMA BRINDES INDUSTRIA E COMERCIO LTDA": "1743",
            "LSMC INTERMEDIACOES E SERVICOS DIGITAIS LTDA": "1744",
            "FFA COMERCIO VAREJISTA DE MATERIAIS PROMOCIONAIS LTDA": "1745",
            "METROPOLES PRODUCOES": "1746",
            "MAZZEL ADVERTISING LTDA": "1747",
            "MULLETS TECNOLOGIA LTDA": "1748",
            "FLASH BALOES COMERCIO DE BALOES - LTDA": "1749",
            "EQUIPE MOSAICO LTDA": "1750",
            "M.C ASSOCIADOS LTDA": "1751",
            "MARILZA ALBUQUERQUE FELIX": "1752",
            "COMERCIAL SA IRMAOS LTDA": "1753",
            "RECIFE TEXTIL": "1754",
            "GREMIO RECREATIVO SOCIO CULTURAL EXPLOSAO INFERNO CORAL": "1766",
            "CONNECTPSP DESENVOLVEDORA DE SISTEMA SA": "1772",
            "W. B. DE OLIVEIRA LTDA": "1776",
            "PATRICIA ROCHA RODRIGUES": "1777",
            "MELIUZ S.A.": "1778",
            "A B OLIVEIRA TRANSPORTE LOCAÇÃO LTDA": "1779",
            "GF SOLUCOES LTDA": "1780",
            "MAIOR DO NORDESTE BRINDES E VARIEDADES LTDA": "1781",
            "INVESTBET LTDA.": "1782",
            "ANNA PAULA DOS SANTOS SILVA 05035506479": "1783",
            "VINICIUS ROBERTO LIMA": "1784",
            "ANTONIO MARCIO DE SANTANA": "1785",
            "MARCUS VINICIUS GUEDES AMBROZIO": "1786",
            "EKKO COPOS E BRINDES LTDA": "1787",
            "JOTA TRES CONFECCAO DE VESTUARIOS LTDA": "1789",
            "TEXTIL LITORAL NORTE LTDA": "1790",
            "MONICA DE LIMA PARRACHO MARTINS": "1792",
            "J. DE L. AZEVEDO": "1793",
            "CAVEIRA TECH NEGOCIOS DIGITAIS LTDA": "1794",
            "SUPER BRINDES LTDA": "1795",
            "S10 STORE LTDA": "1796",
            "PONTES PRODUCOES E EVENTOS LTDA": "1797",
            "FX MARKETING DIGITAL LTDA": "2037",
            "SISTEMA NORDESTE DE COMUNICACAO LTDA": "1822",
            "SANTA CRUZ FUTEBOL CLUBE": "1815",
            "TACAO - CONSULTORIA E ORGANIZACAO ESPORTIVA LTDA": "1828",
            "EMERSON DA SILVA ANUNCIACAO": "1829",
            "LUCAS FELIPE DE LIMA FERREIRA": "1830",
            "BBS SERVICOS E PARTICIPACOES LTDA": "1831",
            "FABRICA ESTUDIOS LTDA": "1832",
            "MATEUS DAMIAO GARCIA": "1833",
            "TABOOLA BRASIL INTERNET LTDA": "1834",
            "OBVIO BRASIL SOFTWARE E SERVIÇOS S.A": "1855",
            "GAMEPLAYS PUBLICIDADE E MERCHANDISING LTDA": "1856",
            "FLAVIANO ANDRE FIDELES GOES": "1858",
            "LENON LEIRAS FREITAS 36926080801": "1859",
            "GEINNY STEPHANE ATAIDE LIMA": "1860",
            "GABRIEL BECHTLUFFT VICTORINO": "1861",
            "AMERICA FUTEBOL CLUBE": "1865",
            "FEDERACAO NACIONAL DAS APAES": "1867",
            "TT CORAL LTDA": "1879",
            "DEFINE DESIGN FABRICACAO DE MATERIAIS PLASTICOS LTDA": "1880",
            "BLACK GAMMING MARKETING E MIDIA DIGITAL LTDA": "1881",
            "M. DE C. MUCELIN LTDA": "1882",
            "NAILSON SILVA DE AGUIAR": "1883",
            "UM TORCEDOR PELO MUNDO LTDA": "1884",
            "RAYANE EWELLIN PORFIRIO DA SILVA MELLO": "1885",
            "ZERO INSTITUICAO DE PAGAMENTO S.A.": "1887",
            "LEANDRO SANTOS DE OLIVEIRA": "1888",
            "ANDERSON FREIRE DOS SANTOS": "1906",
            "PIX DA SORTE CAPITALIZACAO E PROMOCOES LTDA": "1907",
            "GEAN AFONSO SILVA DE CARVALHO": "1927",
            "BMBR MEDIA LTDA": "1928",
            "EBD MANUTENCAO DE EQUIPAMENTOS LTDA": "1929",
            "MARCELO NAVES CHAVES FILHO": "1930",
            "LANA MARKETING LTDA": "1931",
            "LUIZ PAULO WALZERTUDES DANTAS": "1932",
            "CHINA TENDAS LTDA": "1933",
            "INVICTUS AGENCIA LTDA": "1969",
            "OCA SERVIÇOS DE PUBLICIDADE LTDA": "1935",
            "DANYELLE LIMA DOS S DE FARIAS": "1936",
            "SHIRLEY DE TORRES BANDEIRA": "1937",
            "PAULO ANDRE ELIHIMAS MARCONDES": "1938",
            "RR ASSESSORIA EMPRESARIAL LTDA": "1970",
            "OLE INTERACTIVE DO BRASIL LTDA.": "1940",
            "LUCAS MATHEUS MUNIZ DA SILVA": "1950",
            "ROC3 ASSESSORIA EMPRESARIAL LTDA": "1971",
            "FLOW DIGITAL SCALE LTDA": "1972",
            "SPORTS WEB BRASIL - CONTEUDOS DIGITAIS LTDA.": "1973",
            "JOAO THOMAZ DA SILVA OLIVEIRA": "1974",
            "JOAO VITOR ALVES DOS SANTOS": "1975",
            "ONE PLUS ONE PUBLICIDADE LTDA": "1976",
            "ALANA CAROLINA SOARES": "1977",
            "ASSOCIACAO ATLETICA MAGUARY": "1978",
            "ANDRE ANTUNES MENDES MARKETING DIRETO LTDA": "1979",
            "BRUNO SOUSA DE JESUS LTDA": "1980",
            "JULIO CESAR VILAS GOMES": "1981",
            "CARIOCA CONTEUDOS DIGITAIS LTDA": "1982",
            "LEONARDO AMORIM DE ARAUJO": "1983",
            "BANGBANG CONTEUDO EM IMAGENS LTDA": "1984",
            "RAFAEL CONSTANTINO COMERCIO DIGITAL LTDA": "1985",
            "SBR ESPORTES E EMPREENDIMENTOS LTDA": "1986",
            "ISR PRODUCOES E EVENTOS LTDA": "1987",
            "SANTA MARIA EDITORA LTDA": "1988",
            "PJ CONFECCAO DE UNIFORMES LTDA": "1989",
            "ALISON DA SILVA DA ROSA": "1990",
            "BRUNA GISSELY ALBUQUERQUE DA LUZ": "1991",
            "CLEVERSON CARLOS PIMENTEL DIAS TOP SISTEMA": "1992",
            "LIVIO DA SILVA CARDEAL": "1993",
            "PEDRO SPERANDIO JUNIOR": "1994",
            "AUDIENCY BRASIL TECNOLOGIA LTDA": "1995",
            "LINARA MARIA SILVA DE SOUSA QUINTANILHA": "2025",
            "VISIONARY TECH LTDA": "2026",
            "PEGASUS DIGITAL LTDA": "2027",
            "DANIEL ANDRE DA SILVA GAIA": "2028",
            "J LOURENCO DA SILVA": "2029",
            "GESTAO FERRARI SERVICOS ESPECIAIS LTDA": "2030",
            "SIMONETTI ANALISES LTDA": "2032",
            "FLASHSCORE MEDIA LTDA": "2033",
            "LUIZ CARLOS CAVALCANTI": "2034",
            "DETONE COMUNICACAO VISUAL LTDA": "2035",
            "65.055.563 THIAGO WILLIAMS BEZERRA ZILLINGER": "2038",
            "LUAN HENRIQUE GOMES SILVA": "2039",
            "ATIVA TRAVEL VIAGENS E LOCACOES LTDA": "2041"
        }
    },
    "JBD COMUNICACAO E TECNOLOGIA LTDA": {
        "codigo_dominio": "0000",
        "cnpj": "00.000.000/0000-00",
        "codigo_matriz_filial": "3",
        "impostos": {
            '0561': {'n': 'IRRF A RECOLHER', 'c': '178'}, 
            '2172': {'n': 'COFINS A RECOLHER', 'c': '180'}, 
            '8109': {'n': 'PIS A RECOLHER', 'c': '179'},
            'ISS': {'n': 'ISS A RECOLHER', 'c': '173'},
            'INSS': {'n': 'INSS A RECOLHER', 'c': '191'}
        },
        "bancos": {
            'BRASIL': {'n': 'Banco do Brasil', 'r': '8'},
            'RIOPAG MARJOR': {'n': 'Gatway Riopag - Marjorsports', 'r': '9'},
            'SIMPLES': {'n': 'Conta Simples', 'r': '587'},
            'CONTA SIMPLES': {'n': 'Conta Simples', 'r': '587'},
            'PAYBROKERS MAJOR': {'n': 'Paybrokers - Major', 'r': '605'},
            'PAYBROKERS PLAYBONDS': {'n': 'Paybrokers - Playbonds', 'r': '694'},
            'RIOPAG PLAYBONDS': {'n': 'Gatway Riopag - Playbonds', 'r': '641'},
            'RIOPAG CHEGOUBET': {'n': 'Gatway Riopag - Chegoubet', 'r': '640'},
            'TERRA': {'n': 'Banco Terra', 'r': '706'},
            'PAGSTAR': {'n': 'Pagstar', 'r': '842'}
        },
        "fornecedores": {
            "FORNECEDOR DO ESTADO DA PB": "506",
            "FORNECEDOR PARA NOTAS CANCELADAS": "505",
            "ANDERSON DA SILVA VALENTIM": "585",
            "JONAS GABRIEL MUNIZ DE SOUSA": "586",
            "NUTRICARNES C. V. E A. DE CARNES, FRANGO E FRIOS LTDA": "588",
            "MAGAZINE LUIZA S/A": "590",
            "DITONGO CONFECCOES LTDA": "591",
            "ZEINA RASSI SOCIEDADE INDIVIDUAL DE ADVOCACIA": "611",
            "RASSI E QUEIROZ MARCAS E PATENTES LTDA": "612",
            "NGX BRASIL TECNOLOGIA LTDA": "613",
            "BRAMOS ADMINISTRAÇÃO DE OBRAS LTDA": "614",
            "KARL MARX ARRUDA SILVEIRA": "615",
            "49.509.915 GABRIELA DE FREITAS NUNES": "616",
            "52.904.040 PEDRO EMANOEL MARINHO SOUZA": "617",
            "SEBASTIAO JOSE LACERDA DE ANDRADE CONSULTORIA EM TECNOLOGIA DA INFORMACAO LTDA": "618",
            "FLANKR TECNOLOGIA LTDA": "619",
            "CAYO GABRYEL HOLLANDA ANDRADE": "620",
            "52.522.022 LUCAS CORREIA LUCENA DE SOUZA RIBEIRO": "621",
            "JOICE RAFAELA DE ARAUJO FERNANDES": "622",
            "THIAGO FELIPE VIANA DINIZ": "623",
            "52.813.186 JOAO VICTOR MARINHO SOUZA": "624",
            "JOAO CALIXTO DA SILVA NETO": "625",
            "FRANCISCO WELIO FIRMINO DA SILVA JUNIOR": "626",
            "ORBIT TECH SERVICO DE TECNOLOGIA LTDA": "627",
            "GROEN CONSULTORIA EM TECNOLOGIA LTDA": "628",
            "OBVIO BRASIL SOFTWARE E SERVICOS S.A.": "629",
            "ART MAKER COMUNICACAO LTDA": "630",
            "CLUSTER LTDA": "631",
            "ATIVO GAMES LTDA": "691",
            "APPROVE PAYMENT": "646",
            "M D EVANGELISTA": "647",
            "EBTRANS LOGISTICA LTDA": "648",
            "PRIMETIME COMUNICACAO LTDA": "792",
            "THUNDER SERVICOS": "650",
            "ALBUQUERQUE MAIA": "651",
            "SALES MOVEIS": "652",
            "PIXGAMING": "653",
            "ILLUMINARE STUDIO": "654",
            "JP BALOES": "655",
            "JOAO LUCAS COSTA": "656",
            "CABRAL COMERCIO": "657",
            "CLIMARIO": "658",
            "DOM CAFE E SERVICOS DE CAFE": "659",
            "GELAR CLIMATIZAÇÃO": "660",
            "RIBALTA HOTELARIA E TURISMO": "661",
            "EXATO DIGITAL LTDA": "662",
            "VP SOLUCOES EM FECHADURAS": "663",
            "CHURRASCARIA FOGO DE CHAO": "664",
            "RASP NEGOCIOS E INTERMEDIAÇÕES": "665",
            "TV SBT": "666",
            "FRENTE CORRETORA DE CAMBIO": "667",
            "ESCRITÓRIO DR. FEIJÓ": "668",
            "ACG ADMINISTRADORA": "669",
            "ISRAEL MACENA": "670",
            "AUDITOR AUDITORES INDEPENDENTES": "672",
            "RGBC LTDA": "673",
            "EDUARDO CRISTIAN": "674",
            "WALTER VIEIRA DE MELO": "676",
            "LESKA": "675",
            "IAGO ERSON SANTIAGO DE AMARANTE": "677",
            "CAIO CASE DOS SANTOS": "741",
            "DIOGO FERREIRA": "679",
            "VM CONSTRUÇÕES E SO": "680",
            "NEVES E MONTEIRO": "681",
            "J CARLOS COMERCIO ATACADISTA DE MOVEIS EIRELI": "685",
            "SPORTRADAR BRASIL LTDA": "708",
            "RIOPAG S/A": "853",
            "AH MARKETING DIGITAL LTDA.": "710",
            "BRUNO MOURA SILVA": "711",
            "MOD - MARKETING ORIENTADO A DADOS LTDA": "712",
            "MARCO ANTONIO PEREIRA DA SILVA": "713",
            "JUSSIER KELLVIN DE SOUZA": "714",
            "THIERRY MATHEUS BEZERRA DE MELO": "715",
            "ARRUDA COMUNICAÇÃO LTDA": "716",
            "JEFFERSON JORGE DE ARAUJO RODRIGUES": "717",
            "LUPERCIO DAVI FARIAS LUCAS": "718",
            "KAMINO INSTITUIÇÃO DE PAGAMENTO LTDA": "719",
            "HYGOR GONCALVES DUARTE": "720",
            "JESSICA STEPHANNE DA SILVA COSTA": "721",
            "INTERNATIONAL BET ASSESSORIA E CONSULTORIA EM MARKETING DIGITAL LTDA": "722",
            "ERICA CRISTIANE DA SILVA LIMA": "723",
            "CAMILA AYUMI KADO": "724",
            "ISMENIA VITORIA SANTIAGO DE AMARANTE": "725",
            "YASMIN AMELIA FIRMINO": "726",
            "LEANDRO RODRIGUES DE JESUS": "727",
            "GABRIELLA SERRANONE CONRADO": "728",
            "MATHEUS HENRIQUE GUEDES DE OLIVEIRA": "729",
            "ARTHUR TORRES PAIVA LTDA": "730",
            "RISE ADMINISTRACAO LTDA": "731",
            "LUIZ FELIPE FERREIRA DA SILVA": "732",
            "VICTOR NASCIMENTO LIMA": "733",
            "RENAN PHELIPE ASSIS LIMA MAHON": "734",
            "KAIO EDUARDO MIRANDA GOMES": "735",
            "CARLOS RAFAEL FEITOSA RODRIGUES": "736",
            "X7 ASSESSORIA FINANCEIRA - JORGE S ARAUJO": "737",
            "FEIJO E SOUZA SOCIEDADE DE ADVOGADOS": "738",
            "FREITAS E RODRIGUES NEGOCIOS E INTERMEDIACOES LTDA": "739",
            "MAISA GOMES DO NASCIMENTO": "740",
            "JHONATHAN WENDELL DE OLIVEIRA MELO": "742",
            "VINICIUS PREBIL ALCANTARA 44061109847": "743",
            "SALES INDUSTRIA E COMERCIO DE MOVEIS LTDA": "745",
            "BETPASS LTDA": "766",
            "JAMPA BALOES E COMUNICACAO VISUAL LTDA": "767",
            "JANAIRES ALCANTARA DE MEDEIROS": "768",
            "RASP NEGOCIOS E INTERMEDIACOES LTDA": "769",
            "EDUARDO CRISTIAN DE MENDONCA RODRIGUES LTDA": "770",
            "NEVES E MONTEIRO TREINAMENTOS LTDA": "771",
            "GENILZA MENDES DA COSTA": "772",
            "DANNYELLE ALVES DOS SANTOS LUNA": "773",
            "EYTOR FERRAZ GOMES DE MENEZES": "774",
            "NDP ENTRETENIMENTO E VENDAS LTDA": "775",
            "EDILSON MACHADO DO NASCIMENTO": "776",
            "BLACK IA TECNOLOGIA LTDA": "777",
            "MR TV E RADIO WEB CAMPINA GRANDE LTDA": "778",
            "LEC EDUCACAO E PESQUISA LTDA": "779",
            "JONATHAN MARQUES MINDAS": "780",
            "ARTMETAL LTDA": "781",
            "AMANDA GABRIELE LIMA TORRES": "782",
            "IVAH MARKETING & GAMING LTDA": "783",
            "ISRAEL MACENA SILVA": "784",
            "AQUARACE SERVICOS E EVENTOS AQUATICOS LTDA": "785",
            "IVLA MARANHAO SANTOS DE OLIVEIRA": "786",
            "RICHARD L GLOBAL SECURITIES LTDA": "787",
            "MARIA DOS PRAZERES RODRIGUES DA SILVA": "788",
            "CLARIZA IRIS LIMA E SILVA": "789",
            "DANIEL RIBEIRO DE ARAUJO LEITE": "790",
            "TALES DMITRI ARAUJO LOPES": "791",
            "LEGITIMUZ TECNOLOGIA LTDA": "793",
            "XTREMEPUSH LTDA": "794",
            "JACARANDATECH LTDA": "795",
            "TT CAMBIO E TURISMO LTDA": "796",
            "DAXX SOLUTIONS LTDA": "797",
            "ACG INSTITUICAO DE PAGAMENTO S A": "800",
            "SANTIAGO COMUNICACAO E MODA LTDA": "801",
            "WESLLEY PATRICIO GOMES DE OLIVEIRA": "802",
            "CMD BONES": "803",
            "JEFFERSON BARBOSA DO NASCIMENTO": "804",
            "PABLO GADELHA VIANA SOCIEDADE INDIVIDUAL DE ADVOCACIA": "805",
            "RONILDO CASSIO DE CAMPOS & CIA LTDA": "806",
            "ARNALDO FERREIRA DE MENDONCA NETO": "807",
            "RS COMERCIO DE VIDROS E TECNOLOGIA LTDA": "808",
            "FAMILY OFFICE CORPORATE SERVICOS LTDA": "809",
            "AMAURI DE AQUINO GONCALVES 05522799439": "810",
            "PIX GAMING DIGITAL MARKETING LTDA": "811",
            "MB CONSULTORIA ESPORTIVA LTDA": "812",
            "MARIA PRISCILLA DE SOUZA MENEZES": "813",
            "MARIA CAROLINY SANTOS DE MELO": "814",
            "KARTEJANE DEL SANTO DA SILVA": "815",
            "SUPER BRINDES LTDA": "816",
            "TAUANY ZANATA MARTINS": "817",
            "FELIPE GUSTAVO MARTINS DE CASTRO": "818",
            "BAZZANEZE AUDITORES INDEPENDENTES S/S": "819",
            "ANNE SUENIA DA SILVA SALES": "820",
            "M D EVANGELISTA - PRODUCOES": "821",
            "DSA-EVENTOS ESPORTIVOS LTDA": "822",
            "P. I. TEIXEIRA SANTOS": "823",
            "LUCAS MATHEUS MUNIZ DA SILVA": "824",
            "INFOSTARK LTDA": "825",
            "MS DESENVOLVIMENTO E SOLUCOES DIGITAIS LTDA": "826",
            "POUSADA E RECEPTIVO ARIUS LTDA": "827",
            "HOLANDA SUPORTE E CAPACITACOES LTDA": "828",
            "ALEFE GUIMEL LINS BARBOSA CONSULTORIA EM MARKETING LTDA": "829",
            "JOSE LEONARDO FRANCELINO LOPES LTDA": "830",
            "JULLYAN JENNYFER OLIVEIRA PEQUENO": "836",
            "ORLANDO MARCELINO S SANTOS": "832",
            "NARDA MARIA FLORENCIO DOS SANTOS": "833",
            "WALISSON ROMARIO FERREIRA": "834",
            "FELIPE ARRUDA SOCIEDADE INDIVIDUAL DE ADVOCACIA": "837",
            "57.221.901 FRANCYNEIDE GUEDES DE FREITAS ZECA": "838",
            "MARIA HELOISA DE ARAUJO CAMPOS": "839",
            "62.967.608 EDINALDO GOMES DE ARAUJO": "840",
            "SORTE & PROMO LTDA": "843",
            "PLANET INVEST - FOMENTO COMERCIAL LTDA": "844",
            "EVOLUTION SERVICES BRAZIL LTDA": "845",
            "M A SILVA BARBOSA": "846",
            "NAEDJA AGRA CORDEIRO CONFECÇÕES LTDA": "848",
            "J B DIAS LTDA": "849",
            "CAPELLA TECNOLOGIA ACUSTICA LTDA": "850",
            "EGNA DE ARAUJO SILVA": "851",
            "64.669.257 JOAO ANTONIO DE HOLANDA CURVELO SALSA": "852"
        }
    },
    "EMPRESA PADRÃO (Genérica)": {
        "codigo_dominio": "",
        "cnpj": "",
        "codigo_matriz_filial": "",
        "impostos": {
            '0561': {'n': 'IRRF Genérico', 'c': '9999'}, 
            '2172': {'n': 'COFINS Genérico', 'c': '9999'}
        },
        "bancos": {
            'ITAU': {'n': 'Itaú', 'r': '99'}, 
            'BRAD': {'n': 'Bradesco', 'r': '99'}, 
            'SANTANDER': {'n': 'Santander', 'r': '99'}, 
            'BRASIL': {'n': 'B. Brasil', 'r': '99'}
        },
        "fornecedores": {}
    }
}

# Inicializa o Banco de Dados em Memória
if 'empresas_db' not in st.session_state:
    st.session_state['empresas_db'] = BANCO_DE_DADOS_EMPRESAS_INICIAL.copy()

# --- INTERFACE ---
st.title("🏦 Conciliador Contábil")
st.markdown("Automatizado por IA.")

with st.sidebar:
    st.header("🏢 Empresa em Conciliação")
    
    with st.expander("➕ Adicionar Nova Empresa", expanded=False):
        st.markdown("<small>Cadastre uma nova empresa com todos os detalhes contábeis.</small>", unsafe_allow_html=True)
        nova_emp = st.text_input("Nome da Empresa:")
        novo_cod_dominio = st.text_input("Cód. Empresa no Domínio (Para TXT):", "")
        novo_cnpj = st.text_input("CNPJ (Para TXT):", "")
        novo_cod_matriz = st.text_input("Cód. Matriz/Filial (Para CSV):", "")
        novo_banco_nome = st.text_input("Nome do Banco Principal (Ex: ITAU):", "")
        novo_banco_conta = st.text_input("Conta Reduzida do Banco (Ex: 10):", "")
        
        if st.button("Gravar Nova Empresa") and nova_emp:
            if nova_emp not in st.session_state['empresas_db']:
                st.session_state['empresas_db'][nova_emp] = {
                    "codigo_dominio": novo_cod_dominio,
                    "cnpj": novo_cnpj,
                    "codigo_matriz_filial": novo_cod_matriz,
                    "impostos": {'0561': {'n': 'IRRF Padrão', 'c': '9999'}}, 
                    "bancos": {novo_banco_nome.upper() if novo_banco_nome else 'BANCO': {'n': novo_banco_nome if novo_banco_nome else 'Banco Padrão', 'r': novo_banco_conta if novo_banco_conta else '9999'}},
                    "fornecedores": {}
                }
                st.success(f"'{nova_emp}' registada com sucesso!")
                time.sleep(1) 
                st.rerun()
            else:
                st.warning("Esta empresa já existe!")

    empresa_selecionada = st.selectbox(
        "Selecione a base de dados ativa:", 
        list(st.session_state['empresas_db'].keys())
    )
    
    config_atual = st.session_state['empresas_db'][empresa_selecionada]
    
    st.divider()
    st.header("⚙️ Configuração de Importação Domínio")
    st.markdown("<small>Defina os parâmetros de exportação.</small>", unsafe_allow_html=True)
    
    cod_dominio = st.text_input("Cód. Empresa (Para TXT):", config_atual.get("codigo_dominio", "0000"))
    cnpj_empresa = st.text_input("CNPJ da Empresa (Para TXT):", config_atual.get("cnpj", "00.000.000/0000-00"))
    cod_matriz_filial = st.text_input("Código Matriz/Filial (Para CSV):", config_atual.get("codigo_matriz_filial", ""))
    lote_inicial = st.text_input("Número do Lote Inicial (Para CSV):", "890000")
    
    st.session_state['empresas_db'][empresa_selecionada]['codigo_dominio'] = cod_dominio
    st.session_state['empresas_db'][empresa_selecionada]['cnpj'] = cnpj_empresa
    st.session_state['empresas_db'][empresa_selecionada]['codigo_matriz_filial'] = cod_matriz_filial
    
    st.divider()
    st.header("🎯 Natureza da Conciliação")
    modo_conciliacao = st.radio(
        "Filtrar ecrã para:", 
        ["Contas a Pagar (Apenas Débitos/Vermelho)", 
         "Contas a Receber (Apenas Créditos/Verde)", 
         "Ambos (Extrato Completo)"],
        index=0
    )
    
    st.divider()
    st.header("⚙️ Parâmetros")
    ignorar_data = st.checkbox("Ignorar Limite de Datas", value=False, help="Cruza apenas pelo valor exato do Extrato com o Domínio.")
    tolerancia_dias = 99999 if ignorar_data else st.slider("Tolerância de Datas (dias):", 0, 60, 7)
    
    st.divider()
    st.header("🚫 Filtro de Extrato")
    st.markdown("<small>Ignorar linhas que contenham as palavras:</small>", unsafe_allow_html=True)
    ignorar_txt = st.text_area("", "CONNECTPSP, SALDO, RESUMO")
    termos_ignorar = [t.strip().upper() for t in ignorar_txt.split(',')]
    
    st.divider()
    st.header("📋 Plano de Contas (Atalhos Rápidos)")
    
    mapa_imp = {}
    for cod, info in config_atual["impostos"].items():
        nova_conta = st.text_input(f"{info['n']} ({cod})", info['c'], key=f"imp_{empresa_selecionada}_{cod}")
        mapa_imp[cod] = {'conta': nova_conta, 'nome': info['n']}

    mapa_bancos = {}
    for k, v in config_atual["bancos"].items():
        novo_reduzido = st.text_input(f"Cod. {v['n']} ({k})", v['r'], key=f"banco_{empresa_selecionada}_{k}")
        mapa_bancos[k] = {'reduzido': novo_reduzido, 'nome': v['n']}

    mapa_fornecedores = config_atual["fornecedores"]
    
c1, c2 = st.columns(2)
with c1: excel_file = st.file_uploader("📂 Relatório Domínio (Excel/CSV)", type=["xlsx", "xls", "csv"])
with c2: receipt_files = st.file_uploader("📄 PDFs e Extratos Excel/CSV", type=["pdf", "png", "jpg", "xlsx", "xls", "csv"], accept_multiple_files=True)

if excel_file and receipt_files:
    try:
        if excel_file.name.endswith('.csv'):
            try:
                df_dom = pd.read_csv(excel_file, sep=';', encoding='utf-8-sig')
                if len(df_dom.columns) < 2:
                    excel_file.seek(0)
                    df_dom = pd.read_csv(excel_file, sep=',', encoding='utf-8-sig')
            except:
                excel_file.seek(0)
                df_dom = pd.read_csv(excel_file, engine='python')
            df_dom = df_dom.dropna(how='all', axis=1)
        else:
            df_dom = None
            for pular in range(20):
                try:
                    excel_file.seek(0)
                    temp_df = pd.read_excel(excel_file, skiprows=pular)
                    temp_cols = [str(c).lower().strip() for c in temp_df.columns]
                    if any("data" in c or "dt" in c for c in temp_cols) and any("valor" in c or "vlr" in c for c in temp_cols):
                        df_dom = temp_df
                        break
                except Exception:
                    pass
            if df_dom is None:
                excel_file.seek(0)
                df_dom = pd.read_excel(excel_file)
                
        df_dom.columns = [str(c).replace('\n', ' ').strip() for c in df_dom.columns]
        df_dom = df_dom[~df_dom.astype(str).apply(lambda x: x.str.contains('Total Acumulador', case=False, na=False)).any(axis=1)]
        
        c_d, c_v, c_cli, c_nota, c_cfop = None, None, None, None, None
        
        for c in df_dom.columns:
            cl = str(c).lower()
            if not c_d and ("data" in cl or "dt" in cl): c_d = c
            if not c_v and ("valor" in cl or "vlr" in cl or "total" in cl): c_v = c
            if not c_cli and any(x in cl for x in ["fornecedor", "cliente", "nome", "favorecido", "histórico", "historico", "complemento"]): c_cli = c
            if not c_nota and any(x in cl for x in ["nota", "doc", "nfs"]): c_nota = c
            if not c_cfop and "cfop" in cl: c_cfop = c
            
        if not c_d or not c_v:
            st.error(f"❌ ERRO CRÍTICO: Não foi possível localizar as colunas de 'Data' e 'Valor' no ficheiro. Colunas lidas: {', '.join(df_dom.columns)}")
            st.stop()
        
        df_dom = df_dom.reset_index(drop=True)
    except Exception as e:
        st.error(f"Erro ao ler ficheiro: {e}"); st.stop()

    todas_transacoes_pdf = []
    for f in receipt_files:
        with st.spinner(f"A processar {f.name}..."):
            todas_transacoes_pdf.extend(extrair_dados_arquivo(f, mapa_bancos, mapa_imp, True, termos_ignorar))

    mapa_forn_norm = {normalizar_espacos(k): str(v) for k, v in mapa_fornecedores.items()}

    rows, ids_pdf_usados = [], set()
    
    if c_d and c_v:
        for idx, l in df_dom.iterrows():
            v_ex = abs(limpar_valor(l.get(c_v, 0)))
            d_ex_obj = converter_data_dominio(l.get(c_d, None))
            if v_ex == 0 or d_ex_obj is None: continue 
            
            nota_val = l.get(c_nota, "-") if c_nota else "-"
            if pd.isna(nota_val): nota_val = "-"
            if isinstance(nota_val, float) and nota_val.is_integer():
                nota_val = int(nota_val)
            nota_ex = str(nota_val).replace('.0', '') if str(nota_val).endswith('.0') else str(nota_val)
            if nota_ex.lower() == "nan": nota_ex = "-"
            
            is_entrada_dom = False
            if c_cfop and not pd.isna(l.get(c_cfop, None)):
                cfop_str = str(l[c_cfop]).strip()
                if cfop_str.startswith(('5', '6', '7')): is_entrada_dom = True
            else:
                fav_txt = str(l.get(c_cli, '')).upper() if c_cli else ""
                if any(t in fav_txt for t in ["CLIENTE", "RECEBIMENTO", "RECEITA", "DEPOSIT", "GGR", "GROSS", "RENDIMENTO"]):
                    is_entrada_dom = True

            match_found = False
            for i, doc in enumerate(todas_transacoes_pdf):
                if i in ids_pdf_usados: continue
                
                is_credito_pdf = doc.get('Is_Credito', False)
                if is_entrada_dom != is_credito_pdf: continue
                
                for d_pdf_str in doc['Data']:
                    try:
                        d_pdf_obj = datetime.strptime(d_pdf_str, '%d/%m/%Y').date()
                        if abs(v_ex - doc['Total']) < 0.05 and abs((d_ex_obj - d_pdf_obj).days) <= tolerancia_dias:
                            regra_imp = mapa_imp.get(doc['Cod'], {'conta': '9999', 'nome': '-'})
                            b_inf = next((v for k, v in mapa_bancos.items() if k in str(doc['Banc']).upper()), {'nome': doc.get('Banc', 'BANCO'), 'reduzido': '9999'})
                            
                            fav_final = str(l.get(c_cli, '')).upper() if c_cli else ""
                            if fav_final == "NAN" or not fav_final: fav_final = doc['Fav']
                            
                            fav_final_clean = normalizar_espacos(fav_final)
                            conta_contrapartida = '9999'
                            nome_contrapartida = fav_final 
                            
                            if regra_imp['nome'] != '-':
                                conta_contrapartida = regra_imp['conta']
                                nome_contrapartida = regra_imp['nome']
                            else:
                                if fav_final_clean in mapa_forn_norm:
                                    conta_contrapartida = mapa_forn_norm[fav_final_clean]
                                else:
                                    for f_nome, f_conta in mapa_forn_norm.items():
                                        if f_nome in fav_final_clean or fav_final_clean in f_nome:
                                            conta_contrapartida = f_conta
                                            break
                                            
                            str_imposto = formatar_codigo_nome(doc['Cod'], regra_imp['nome']) if regra_imp['nome'] != '-' else "-"
                            str_favorecido = formatar_codigo_nome(conta_contrapartida, fav_final)
                            
                            str_banco = formatar_codigo_nome(b_inf['reduzido'], b_inf['nome'])
                            str_contrapartida = formatar_codigo_nome(conta_contrapartida, nome_contrapartida)

                            if is_entrada_dom:
                                str_debito = str_banco
                                str_credito = str_contrapartida
                                val_entrada = v_ex
                                val_saida = 0.0
                            else:
                                str_debito = str_contrapartida
                                str_credito = str_banco
                                val_entrada = 0.0
                                val_saida = v_ex

                            rows.append({
                                'Status': '✅ CONCILIADO', 'Data Excel': d_ex_obj.strftime('%d/%m/%Y'), 'Nota': nota_ex,
                                'Valor Total': v_ex, 'Entradas': val_entrada, 'Saídas': val_saida,
                                'Imposto': str_imposto, 'Favorecido': str_favorecido, 'Data PDF': d_pdf_obj.strftime('%d/%m/%Y'),
                                'Banco': b_inf['nome'], 'Débito': str_debito, 'Crédito': str_credito, 
                                'Principal': doc.get('Principal', v_ex), 'Multa': doc.get('Multa', 0.0), 'Juros': doc.get('Juros', 0.0),
                                'Cód. Receita': doc['Cod'], 'Arquivo': doc['Arq']
                            })
                            ids_pdf_usados.add(i); match_found = True; break
                    except: continue
                if match_found: break
                
            if not match_found:
                if "Pagar" in modo_conciliacao and is_entrada_dom: continue
                if "Receber" in modo_conciliacao and not is_entrada_dom: continue
                
                val_entrada = v_ex if is_entrada_dom else 0.0
                val_saida = v_ex if not is_entrada_dom else 0.0
                fav_cli = str(l.get(c_cli, '')).upper() if c_cli else ""
                
                fav_cli_clean = normalizar_espacos(fav_cli)
                conta_pendente = '9999'
                if fav_cli_clean in mapa_forn_norm:
                    conta_pendente = mapa_forn_norm[fav_cli_clean]
                else:
                    for f_nome, f_conta in mapa_forn_norm.items():
                        if f_nome in fav_cli_clean or fav_cli_clean in f_nome:
                            conta_pendente = f_conta
                            break
                
                rows.append({
                    'Status': '❌ Só no Domínio', 'Data Excel': d_ex_obj.strftime('%d/%m/%Y'), 'Nota': nota_ex,
                    'Valor Total': v_ex, 'Entradas': val_entrada, 'Saídas': val_saida,
                    'Imposto': '-', 'Favorecido': formatar_codigo_nome(conta_pendente, fav_cli), 'Data PDF': '-',
                    'Banco': '-', 'Débito': '-', 'Crédito': '-', 
                    'Principal': '-', 'Multa': '-', 'Juros': '-',
                    'Cód. Receita': '-', 'Arquivo': '-'
                })

    for i, doc in enumerate(todas_transacoes_pdf):
        if i not in ids_pdf_usados:
            is_credito_pdf = doc.get('Is_Credito', False)
            
            if "Pagar" in modo_conciliacao and is_credito_pdf: continue
            if "Receber" in modo_conciliacao and not is_credito_pdf: continue
            
            b_inf = next((v for k, v in mapa_bancos.items() if k in str(doc['Banc']).upper()), {'nome': doc.get('Banc', 'BANCO'), 'reduzido': '9999'})
            
            fav_pdf = normalizar_espacos(doc['Fav'])
            conta_contrapartida = '9999'
            nome_contrapartida = doc['Fav']
            
            if fav_pdf in mapa_forn_norm: conta_contrapartida = mapa_forn_norm[fav_pdf]
            else:
                for f_nome, f_conta in mapa_forn_norm.items():
                    if f_nome in fav_pdf or fav_pdf in f_nome:
                        conta_contrapartida = f_conta
                        break
                        
            regra_imp = mapa_imp.get(doc['Cod'], {'conta': '9999', 'nome': '-'})
            str_imposto = formatar_codigo_nome(doc['Cod'], regra_imp['nome']) if regra_imp['nome'] != '-' else "-"
            
            str_banco = formatar_codigo_nome(b_inf['reduzido'], b_inf['nome'])
            str_contrapartida = formatar_codigo_nome(conta_contrapartida, nome_contrapartida)

            if is_credito_pdf:
                str_debito = str_banco
                str_credito = str_contrapartida
                val_entrada = doc['Total']
                val_saida = 0.0
            else:
                str_debito = str_contrapartida
                str_credito = str_banco
                val_entrada = 0.0
                val_saida = doc['Total']
            
            rows.append({
                'Status': '⚠️ Só no Extrato', 'Data PDF': doc['Data'][0], 'Nota': '-',
                'Valor Total': doc['Total'], 'Entradas': val_entrada, 'Saídas': val_saida,
                'Imposto': str_imposto, 'Favorecido': formatar_codigo_nome(conta_contrapartida, fav_pdf), 
                'Banco': b_inf['nome'], 'Débito': str_debito, 'Crédito': str_credito, 'Arquivo': doc['Arq']
            })

    res_df = pd.DataFrame(rows).fillna("-")
    st.subheader(f"📋 Relatório de Conciliação - {empresa_selecionada}")
    disp = res_df.copy()
    
    col_order = ['Status', 'Data Excel', 'Nota', 'Valor Total', 'Entradas', 'Saídas', 'Imposto', 'Favorecido', 'Data PDF', 'Banco', 'Débito', 'Crédito', 'Principal', 'Multa', 'Juros', 'Cód. Receita', 'Arquivo']
    disp = disp[[c for c in col_order if c in disp.columns]]
    
    for col in ['Valor Total', 'Entradas', 'Saídas', 'Principal', 'Multa', 'Juros']:
        if col in disp.columns: disp[col] = disp[col].apply(formatar_moeda)

    def color_status(val):
        color = 'rgba(46, 204, 113, 0.08)' if val == '✅ CONCILIADO' else 'rgba(231, 76, 60, 0.08)' if val == '❌ Só no Domínio' else 'rgba(241, 196, 15, 0.08)'
        return f'background-color: {color}'

    styled = disp.style.map(color_status, subset=['Status']) if hasattr(disp.style, 'map') else disp.style.applymap(color_status, subset=['Status'])
    st.dataframe(styled, use_container_width=True)
    
    # ---------------------------------------------------------
    # EXPORTAÇÃO COM FILTROS DE STATUS E BLINDAGEM DE CÓDIGOS
    # ---------------------------------------------------------
    st.divider()
    st.header("📥 Opções de Exportação")
    st.markdown("Escolha o que deseja incluir nos ficheiros finais:")
    
    filtro_exportacao = st.radio(
        "Quais lançamentos deseja exportar para o Domínio?",
        ["Todos os Lançamentos (Conciliados e Pendentes)", 
         "Apenas ✅ CONCILIADOS (Mais Seguro)", 
         "Apenas Pendentes (❌ Domínio e ⚠️ Extrato)"],
        index=0
    )
    
    df_export = res_df.copy()
    if filtro_exportacao == "Apenas ✅ CONCILIADOS (Mais Seguro)":
        df_export = df_export[df_export['Status'] == '✅ CONCILIADO']
    elif filtro_exportacao == "Apenas Pendentes (❌ Domínio e ⚠️ Extrato)":
        df_export = df_export[df_export['Status'] != '✅ CONCILIADO']
        
    codigos_bancos_atuais = [str(v['r']).strip() for v in config_atual["bancos"].values()]
    
    # 1. TXT (Domínio Antigo)
    txt_content = gerar_txt_dominio(df_export, cod_dominio, cnpj_empresa, codigos_bancos_atuais)
    nome_arquivo_txt = f"Importacao_Dominio_{empresa_selecionada.split()[0].upper()}.txt"
    txt_bytes = txt_content.encode('iso-8859-1', errors='replace')

    # 2. CSV (Domínio Novo) - Blindado contra contas vazias ou com "0"
    def extrair_conta_limpa(texto):
        m = re.search(r'^(\d+)', str(texto).strip())
        cod = m.group(1) if m else "9999"
        if cod == "0" or cod == "00": return "9999"
        return cod

    df_valido = df_export[df_export['Valor Total'].apply(limpar_valor) > 0].copy()
    linhas_dominio = []
    
    try: lote_atual = int(lote_inicial)
    except: lote_atual = 890000

    for idx, row in df_valido.iterrows():
        val = limpar_valor(row['Valor Total'])
        if val <= 0: continue
        
        cod_deb = extrair_conta_limpa(row['Débito'])
        cod_cred = extrair_conta_limpa(row['Crédito'])
        
        nota_val = str(row.get('Nota', '-')).strip()
        texto_nota = f" NFS {nota_val}" if nota_val and nota_val != '-' else ""
        
        status = str(row.get('Status', ''))
        favorecido = str(row['Favorecido']).split(' - ')[-1].strip()
        if not favorecido or favorecido == "-": favorecido = "LANCAMENTO CONTABIL"
        
        if "Domínio" in status:
            prefixo = texto_nota.strip()
            hist_final = f"{prefixo} - {favorecido.upper()}" if prefixo else favorecido.upper()
        else:
            # Inteligência baseada puramente na conta do Banco
            if cod_deb in codigos_bancos_atuais:
                tipo_hist = "RECBTO"
            elif cod_cred in codigos_bancos_atuais:
                tipo_hist = "PAGTO"
            else:
                val_entrada = limpar_valor(row.get('Entradas', 0))
                tipo_hist = "RECBTO" if val_entrada > 0 else "PAGTO"
            
            hist_final = f"{tipo_hist}{texto_nota} - {favorecido.upper()}"
            
        data_lanc = row['Data Excel'] if row['Data Excel'] != '-' else row['Data PDF']
        try: data_str = datetime.strptime(str(data_lanc), '%d/%m/%Y').strftime('%d/%m/%Y')
        except: data_str = str(data_lanc)
            
        valor_formatado = f"{val:.2f}".replace('.', ',')
        
        linhas_dominio.append({
            'Data': data_str,
            'Cód. Conta Debito': cod_deb,
            'Cód. Conta Credito': cod_cred,
            'Valor': valor_formatado,
            'Cód. Histórico': '',
            'Complemento Histórico': hist_final[:250],
            'Inicia Lote': '',
            'Código Matriz/Filial': '',
            'Centro de Custo Débito': '',
            'Centro de Custo Crédito': '',
            'Status Conciliação': row['Status'] 
        })
        lote_atual += 1
        
    df_dominio_export = pd.DataFrame(linhas_dominio)
    
    if not df_dominio_export.empty:
        csv_string = df_dominio_export.to_csv(sep=';', index=False)
        csv_bytes = csv_string.encode('iso-8859-1', errors='replace')
    else:
        csv_bytes = "Não existem dados com este filtro.".encode('utf-8')
        
    nome_arquivo_csv = f"Importacao_Dominio_{empresa_selecionada.split()[0].upper()}.csv"

    col_dl1, col_dl2 = st.columns(2)
    with col_dl1:
        st.download_button("📄 Baixar Arquivo Domínio (TXT)", txt_bytes, nome_arquivo_txt, mime="text/plain")
    with col_dl2:
        st.download_button("🚀 Baixar Arquivo Domínio (CSV)", csv_bytes, nome_arquivo_csv, mime="text/csv")
