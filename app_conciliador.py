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
    """Junta o código contábil ao nome."""
    cod_str = str(codigo).strip()
    if cod_str.endswith('.0'): cod_str = cod_str[:-2]
    if not cod_str or cod_str in ['9999', '99', 'nan', '-']: return str(nome)
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
            if file.name.lower().endswith('.csv'): df_ext = pd.read_csv(file, engine='python')
            else: df_ext = pd.read_excel(file)
            
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

# --- EXPORTAÇÃO CSV FORMATO DOMÍNIO ---
def gerar_csv_dominio(df_conciliado, lote_inicial):
    """Gera um DataFrame com o padrão exato CSV/XLSX exigido pelo Domínio."""
    linhas = []
    
    # Extrair contas ignorando a parte do texto (Ex: de "1107 - Delfinance" extrai "1107")
    def extrair_conta(texto):
        m = re.search(r'^(\d+)', str(texto).strip())
        return m.group(1) if m else ""
    
    # Processar apenas as linhas com valor financeiro conciliadas e sobras
    df_valido = df_conciliado[df_conciliado['Valor Total'].apply(limpar_valor) > 0].copy()
    
    try:
        lote_atual = int(lote_inicial)
    except:
        lote_atual = 890000

    for idx, row in df_valido.iterrows():
        val = limpar_valor(row['Valor Total'])
        if val <= 0: continue
        
        cod_deb = extrair_conta(row['Débito'])
        cod_cred = extrair_conta(row['Crédito'])
        
        data_lanc = row['Data Excel'] if row['Data Excel'] != '-' else row['Data PDF']
        try:
            data_str = datetime.strptime(str(data_lanc), '%d/%m/%Y').strftime('%d/%m/%Y')
        except:
            data_str = str(data_lanc)
            
        favorecido = str(row['Favorecido']).split(' - ')[-1].strip()
        if not favorecido or favorecido == "-": favorecido = "LANCAMENTO CONTABIL"
        
        # Converte o valor para o formato português com vírgula para o CSV
        valor_formatado = f"{val:.2f}".replace('.', ',')
        
        linhas.append({
            'Data': data_str,
            'Cód. Conta Debito': cod_deb,
            'Cód. Conta Credito': cod_cred,
            'Valor': valor_formatado,
            'Cód. Histórico': '',
            'Complemento Histórico': favorecido.upper()[:250],
            'Inicia Lote': lote_atual,
            'Código Matriz/Filial': '',
            'Centro de Custo Débito': '',
            'Centro de Custo Crédito': ''
        })
        lote_atual += 1
        
    return pd.DataFrame(linhas)

# ==========================================
# 🧠 BANCO DE DADOS INTEGRADO
# ==========================================
BANCO_DE_DADOS_EMPRESAS_INICIAL = {
    "SELECT OPERATIONS S.A.": {
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
            'CACTUS TECNOLOGIA': '1223',
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
            'LEGITIMUZ TECNOLOGIA LTDA': '1372',
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
            'AVANT EXPANSAO DE FRANQUIAS LTDA': '1312',
            'DANTAS CM & AM LTDA': '1313',
            'TAISSUKE LOCACOES LTDA': '1557',
            'JOYO TECNOLOGIA BRASIL LTDA.': '1315',
            'SELBR SERVICE LTDA': '1316',
            'MOZART RODRIGUES CASTELLO SOCIEDADE INDIVIDUAL DE ADVOCACIA': '1317',
            'AFILIAPIX SOLUCOES EM MARKETING E TECNOLOGIA LTDA': '1318',
            'CHECKMATE MARKETING DIGITAL LTDA': '1319',
            'STEPHANY DOS SANTOS REIS': '1320',
            'BRAFIN SOLUCOES, INTERMEDIACAO E PAGAMENTOS LTDA': '1321',
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
            'ROMARIO RODRIGUES': '1878',
            'PYERRE SAYMON DE MELO SILVA': '1513',
            'INTERNATIONAL BET ASSESSORIA E CONSULTORIA EM MARKETING DIGITAL LTDA': '474',
            'DIEGO HENRIQUE SANTOS DE SANTANA': '47',
            'RT BRASIL CONSULTORIA E EMPREENDIMENTOS FINANCEIROS LTDA': '383',
            '60.692.475 SIDNEY ALVES CORREIA JUNIOR': '490',
            '65.227.051 LUIZ HENRIQUE DOS SANTOS GONZAGA': '494',
            'PAGLIVRE SOLUCOES EM COBRANCA LTDA': '425',
            'DUCAMPELO PARTICIPACOES LTDA': '476',
            '64.438.924 GABRIELLA BORGES ROCHA': '477',
            'LEGITIMUZ TECNOLOGIA LTDA': '1372',
            'UNIFICAPAY SERVICOS FINANCEIROS E DE PAGAMENTOS LTDA': '760', 
            'AM PUBLICIDADE E PROMOCAO DE VENDAS LTDA': '1250' 
        }
    },
    "PIXBET SOLUCOES TECNOLOGICAS LTDA": {
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
        "fornecedores": {}
    },
    "JBD COMUNICACAO E TECNOLOGIA LTDA": {
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
        "fornecedores": {}
    },
    "EMPRESA PADRÃO (Genérica)": {
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

# Inicializa o Banco de Dados em Memória (Para adicionar novas empresas ao vivo)
if 'empresas_db' not in st.session_state:
    st.session_state['empresas_db'] = BANCO_DE_DADOS_EMPRESAS_INICIAL.copy()

# --- INTERFACE ---
st.title("🏦 Conciliador Contábil IA V42.0")
st.markdown("Integração Perfeita com **Domínio Sistemas** (Exportação CSV) e Gestão Dinâmica de Empresas.")

with st.sidebar:
    st.header("🏢 Empresa em Conciliação")
    
    # --- SISTEMA PARA ADICIONAR NOVA EMPRESA ---
    with st.expander("➕ Adicionar Nova Empresa", expanded=True):
        st.markdown("<small>Crie uma nova empresa para conciliar rapidamente.</small>", unsafe_allow_html=True)
        nova_emp = st.text_input("Nome da Empresa:")
        if st.button("Gravar Nova Empresa") and nova_emp:
            if nova_emp not in st.session_state['empresas_db']:
                st.session_state['empresas_db'][nova_emp] = {
                    "impostos": {'0561': {'n': 'IRRF Padrão', 'c': '9999'}}, 
                    "bancos": {'BANCO': {'n': 'Banco Padrão', 'r': '9999'}},
                    "fornecedores": {}
                }
                st.success(f"'{nova_emp}' registada com sucesso!")
                time.sleep(1) # Dá tempo para ler a mensagem antes de atualizar
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
    st.markdown("<small>Defina o Lote Inicial que o Domínio vai usar ao importar o CSV.</small>", unsafe_allow_html=True)
    lote_inicial = st.text_input("Número do Lote Inicial:", "890000")
    
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
            df_dom = pd.read_csv(excel_file, engine='python')
            df_dom = df_dom.dropna(how='all', axis=1)
        else:
            # SCANNER DE CABEÇALHO
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
        
        c_d = next((c for c in df_dom.columns if "data" in c.lower() or "dt" in c.lower()), None)
        c_v = next((c for c in df_dom.columns if "valor" in c.lower() and "cont" in c.lower()), next((c for c in df_dom.columns if "valor" in c.lower() or "vlr" in c.lower()), None))
        
        if c_d is None or c_v is None:
            st.error("❌ ERRO CRÍTICO: Não foi possível localizar as colunas 'Data' e 'Valor' no ficheiro do Domínio.")
            st.stop()
            
        c_cli = next((c for c in df_dom.columns if any(x in c.lower() for x in ["fornecedor", "cliente", "nome"])), "Fornecedor")
        c_nota = next((c for c in df_dom.columns if any(x in c.lower() for x in ["nota", "doc", "núm", "num"])), None)
        c_cfop = next((c for c in df_dom.columns if "cfop" in str(c).lower()), None)
        
        # ROBÔ AUTODIDATA
        c_cod_cli = None
        if c_cli in df_dom.columns:
            idx_cli = df_dom.columns.get_loc(c_cli)
            if idx_cli > 0: c_cod_cli = df_dom.columns[idx_cli - 1]
        
        df_dom = df_dom.reset_index(drop=True)
    except Exception as e:
        st.error(f"Erro ao ler ficheiro: {e}"); st.stop()

    todas_transacoes_pdf = []
    for f in receipt_files:
        with st.spinner(f"A processar {f.name}..."):
            todas_transacoes_pdf.extend(extrair_dados_arquivo(f, mapa_bancos, mapa_imp, True, termos_ignorar))

    mapa_forn_norm = {normalizar_espacos(k): str(v) for k, v in mapa_fornecedores.items()}
    
    if c_cli and c_cod_cli:
        for _, l in df_dom.iterrows():
            nome_dom = str(l[c_cli]).upper().strip()
            cod_dom = str(l[c_cod_cli]).replace('.0', '').strip()
            if nome_dom and nome_dom != 'NAN' and cod_dom and cod_dom != 'NAN' and cod_dom != 'NONE':
                mapa_forn_norm[normalizar_espacos(nome_dom)] = cod_dom

    rows, ids_pdf_usados = [], set()
    for idx, l in df_dom.iterrows():
        v_ex = abs(limpar_valor(l[c_v]))
        d_ex_obj = converter_data_dominio(l[c_d])
        if v_ex == 0 or d_ex_obj is None: continue 
        
        nota_val = l[c_nota] if c_nota and not pd.isna(l[c_nota]) else "-"
        if isinstance(nota_val, float) and nota_val.is_integer():
            nota_val = int(nota_val)
        nota_ex = str(nota_val).replace('.0', '') if str(nota_val).endswith('.0') else str(nota_val)
        if nota_ex == "nan": nota_ex = "-"
        
        codigo_excel = ""
        if c_cod_cli and not pd.isna(l[c_cod_cli]):
            codigo_excel = str(l[c_cod_cli]).replace('.0', '').strip()
            if codigo_excel == "nan" or codigo_excel == "None": codigo_excel = ""
        
        is_entrada_dom = False
        if c_cfop and not pd.isna(l[c_cfop]):
            cfop_str = str(l[c_cfop]).strip()
            if cfop_str.startswith(('5', '6', '7')): is_entrada_dom = True
        else:
            fav_txt = str(l.get(c_cli, '')).upper()
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
                        
                        fav_final = str(l.get(c_cli, '')).upper()
                        if fav_final == "NAN" or not fav_final: fav_final = doc['Fav']
                        
                        fav_final_clean = normalizar_espacos(fav_final)
                        conta_contrapartida = '9999'
                        nome_contrapartida = fav_final 
                        
                        if regra_imp['nome'] != '-':
                            conta_contrapartida = regra_imp['conta']
                            nome_contrapartida = regra_imp['nome']
                        else:
                            if codigo_excel:
                                conta_contrapartida = codigo_excel
                            elif fav_final_clean in mapa_forn_norm:
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
            fav_cli = str(l.get(c_cli, '')).upper()
            
            rows.append({
                'Status': '❌ Só no Domínio', 'Data Excel': d_ex_obj.strftime('%d/%m/%Y'), 'Nota': nota_ex,
                'Valor Total': v_ex, 'Entradas': val_entrada, 'Saídas': val_saida,
                'Imposto': '-', 'Favorecido': formatar_codigo_nome(codigo_excel if codigo_excel else '9999', fav_cli), 'Data PDF': '-',
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
    # EXPORTAÇÃO E DOWNLOAD DE ARQUIVOS (EXCEL VISUAL E CSV DOMÍNIO)
    # ---------------------------------------------------------
    
    # 1. Gera o Excel Visual Completo (Conciliação)
    out_excel_visual = io.BytesIO()
    with pd.ExcelWriter(out_excel_visual, engine='xlsxwriter') as wr: res_df.to_excel(wr, index=False)
    nome_arquivo_excel = f"conciliacao_{empresa_selecionada.split()[0].lower()}.xlsx"

    # 2. Gera o Ficheiro Padrão CSV/XLSX para Domínio
    def extrair_conta_limpa(texto):
        m = re.search(r'^(\d+)', str(texto).strip())
        return m.group(1) if m else ""

    df_valido = res_df[res_df['Valor Total'].apply(limpar_valor) > 0].copy()
    linhas_dominio = []
    
    try: lote_atual = int(lote_inicial)
    except: lote_atual = 890000

    for idx, row in df_valido.iterrows():
        val = limpar_valor(row['Valor Total'])
        if val <= 0: continue
        
        cod_deb = extrair_conta_limpa(row['Débito'])
        cod_cred = extrair_conta_limpa(row['Crédito'])
        
        data_lanc = row['Data Excel'] if row['Data Excel'] != '-' else row['Data PDF']
        try: data_str = datetime.strptime(str(data_lanc), '%d/%m/%Y').strftime('%d/%m/%Y')
        except: data_str = str(data_lanc)
            
        favorecido = str(row['Favorecido']).split(' - ')[-1].strip()
        if not favorecido or favorecido == "-": favorecido = "LANCAMENTO CONTABIL"
        
        # Converte o valor para o formato português (com vírgula)
        valor_formatado = f"{val:.2f}".replace('.', ',')
        
        linhas_dominio.append({
            'Data': data_str,
            'Cód. Conta Debito': cod_deb,
            'Cód. Conta Credito': cod_cred,
            'Valor': valor_formatado,
            'Cód. Histórico': '',
            'Complemento Histórico': favorecido.upper()[:250],
            'Inicia Lote': lote_atual,
            'Código Matriz/Filial': '',
            'Centro de Custo Débito': '',
            'Centro de Custo Crédito': ''
        })
        lote_atual += 1
        
    df_dominio_export = pd.DataFrame(linhas_dominio)
    
    # Exporta para CSV com delimitador de ponto e vírgula e encoding pt-BR/latin1
    csv_buffer = df_dominio_export.to_csv(sep=';', index=False, encoding='utf-8-sig')
    nome_arquivo_csv = f"Importacao_Dominio_{empresa_selecionada.split()[0].upper()}.csv"

    col_dl1, col_dl2 = st.columns(2)
    with col_dl1:
        st.download_button("📥 Baixar Relatório de Conciliação (XLSX)", out_excel_visual.getvalue(), nome_arquivo_excel)
    with col_dl2:
        st.download_button("🚀 Baixar Arquivo Domínio (CSV)", csv_buffer, nome_arquivo_csv, mime="text/csv")
