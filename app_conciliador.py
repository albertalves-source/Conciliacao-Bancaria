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
    v_str = str(v).replace('R$', '').replace('$', '').replace(' ', '').replace('.', '').strip()
    if ',' in v_str:
        v_str = v_str.replace(',', '.')
    try: return float(v_str)
    except:
        # Tenta capturar o valor se houver sujeira de string ao redor
        match = re.search(r'([\d,.]+)', v_str)
        if match:
            try: return float(match.group(1).replace(',', '.'))
            except: return 0.0
        return 0.0

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
        # Tenta formato AAAA-MM-DD
        match_iso = re.search(r'(\d{4}-\d{2}-\d{2})', str(data_obj))
        if match_iso: return datetime.strptime(match_iso.group(1), '%Y-%m-%d').date()
        return None

def normalizar_espacos(texto):
    if not isinstance(texto, str): return ""
    return " ".join(texto.upper().split())

def formatar_codigo_nome(codigo, nome):
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
                    if java_linha := linha_temp: linhas_agrupadas.append(java_linha)
                    
                    for linha in linhas_agrupadas:
                        linha_upper = linha.upper()
                        if any(x in linha_upper for x in ["SALDO INICIAL", "SALDO FINAL", "RESUMO", "DISPONÍVEL", "VALOR TOTAL", "TOTAL ACUMULADOR"]): continue
                        
                        is_credito = False
                        if any(x in linha_upper for x in ["RECEBID", "DEVOLU", "ESTORNO", "CREDITO", "CRÉDITO", "DEPÓSITO", "TED RECEBIDA"]):
                            is_credito = True
                        
                        if any(t in linha_upper for t in termos_ignorar if t): continue
                        
                        data_match = re.search(r'(\d{2}/\d{2}/\d{4})', linha)
                        valor_match = re.findall(r'-?\b\d{1,3}(?:\.\d{3})*,\d{2}\b', linha)
                        
                        if data_match and valor_match:
                            desc_bruta = linha.replace(data_match.group(1), "")
                            for v_txt in valor_match: desc_bruta = desc_bruta.replace(v_txt, "")
                            
                            nome_limpo = re.sub(r'[A-Z0-9]{8}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{12}', '', desc_bruta.upper())
                            nome_limpo = re.sub(r'\b[A-Z0-9]*\d[A-Z0-9]*\b', '', nome_limpo)
                            for t in ["PAGAMENTO VIA PIX", "PAGAMENTO DE BOLETO", "TRANSFERENCIA INTERNA"]:
                                nome_limpo = nome_limpo.replace(t, '')
                            nome_limpo = normalizar_espacos(nome_limpo)
                            
                            if not nome_limpo: 
                                if "BOLETO" in linha_upper: nome_limpo = "PAGAMENTO DE BOLETO"
                                else: nome_limpo = "TRANSFERENCIA BANCARIA"

                            cod_found = ""
                            for c in re.findall(r'\b(\d{4})\b', linha):
                                if c in mapa_imp: cod_found = c; break
                            
                            # Captura o último valor da linha (geralmente o valor do movimento, antes do saldo)
                            val = abs(limpar_valor(valor_match[0]))
                            if val > 0:
                                transacoes.append({
                                    'Data': data_match.group(1), 'Total': val,
                                    'Cod': cod_found, 'Fav': nome_limpo, 
                                    'Banc': banco_base if banco_base else "Z.RO BANK", 'Arq': file.name,
                                    'Is_Credito': is_credito
                                })
        except Exception as e: 
            st.error(f"Erro ao ler PDF: {e}")
        
    elif file.name.lower().endswith((".xlsx", ".xls", ".csv")):
        try:
            if file.name.lower().endswith('.csv'):
                try: df_ext = pd.read_csv(file, sep=';', encoding='utf-8-sig')
                except: df_ext = pd.read_csv(file, sep=',', encoding='utf-8-sig')
            else:
                df_ext = pd.read_excel(file)
            
            for index, row in df_ext.iterrows():
                linha_parts = [str(v) for v in row.values if not pd.isna(v)]
                linha = " ".join(linha_parts).upper()
                if any(x in linha for x in ["SALDO", "TOTAL"]) or not linha_parts: continue
                
                is_credito = True if "RECEB" in linha or "CRED" in linha else False
                data_match = re.search(r'(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})', linha)
                valor_match = re.findall(r'-?\b\d{1,3}(?:\.\d{3})*,\d{2}\b', linha)
                
                if data_match and valor_match:
                    val = abs(limpar_valor(valor_match[0]))
                    transacoes.append({
                        'Data': data_match.group(1), 'Total': val, 'Cod': '', 'Fav': normalizar_espacos(linha[:50]), 
                        'Banc': "EXTRATO", 'Arq': file.name, 'Is_Credito': is_credito
                    })
        except Exception as e: st.warning(f"Erro ao ler extrato: {e}")
            
    return transacoes

# ==========================================
# 🧠 BANCO DE DADOS INTEGRADO
# ==========================================
BANCO_DE_DADOS_EMPRESAS_INICIAL = {
    "PIXBET SOLUCOES TECNOLOGICAS LTDA": {
        "codigo_dominio": "1002",
        "cnpj": "40.633.348/0001-30",
        "codigo_matriz_filial": "1",
        "impostos": {
            'IRRF': {'n': 'IRRF RETIDO', 'c': '9999'}, 
            'CRF': {'n': 'CRF RETIDO', 'c': '9999'}
        },
        "bancos": {
            'Z.RO': {'n': 'Z.RO BANK', 'r': '8281458'}
        },
        "fornecedores": {
            "EBD MANUTENCAO DE EQUIPAMENTOS LTDA": "508",
            "RECPARK ESTACIONAMENTOS LTDA": "948",
            "ESMERA EMPREENDIMENTOS IMOBILIARIOS LTDA": "949",
            "CRA SERVICOS TERCEIRIZADOS LTDA": "950",
            "FACIL TRANSFER COMERCIO DE CAMISAS LTDA": "118",
            "NILSON JOSE CARMO DA SILVA FILHO LTDA": "222",
            "GONCALO JOAO PONTES NETO 08967341458": "900",
            "53.213.098 JOAO CHALACA SOUZA LEAO": "902",
            "55.157.363 LUIZ ORLANDO BARBOSA DO PATROCINIO OLIVEIRA": "953",
            "SANTOS PRESTACAO DE SERVIÇOS LTDA": "831",
            "63.553.437 LEANDRO SANTOS DE OLIVEIRA": "472",
            "NAHSOM VIDEO PRODUCOES LTDA": "805",
            "AUDIOLA PRODUCOES DE AUDIO E MUSICA LTDA": "807",
            "D A F ARAGAO SERVIÇOS": "774",
            "DF DIGITAL MARKETING LTDA": "778",
            "VICTOR CORDEIRO DE MELO 09727327486": "791",
            "UM TORCEDOR PELO MUNDO LTDA": "470",
            "ANTONIO MARCIO DE SANTANA": "398",
            "TGF DIGITAL MARKETING LTDA": "137",
            "57.524.934 ADRIANO DA CONCEICAO SOUZA": "90",
            "MC4 PROMO MARKETING DIRETO LTDA": "17",
            "RR ASSESSORIA EMPRESARIAL LTDA": "590",
            "ROC3 ASSESSORIA EMPRESARIAL LTDA": "591",
            "SBR ESPORTES E EMPREENDIMENTOS LTDA": "592",
            "ADMASTERS SOLUCOES DE MARKETING DIGITAL LTDA": "145",
            "LANA MARKETING LTDA": "499",
            "PI X GAMING DIGITAL MARKETING LTDA": "84",
            "47.400.762 MATHEUS NAGY LARIOS": "849",
            "MAILINBOX COMUNICACOES LTDA": "146",
            "DEJO DO BRASIL LTDA": "861",
            "RALI NEGOCIOS DIGITAIS LTDA": "209",
            "CAM MOBILE SERVICOS DE TELEFONIA LTDA": "926",
            "61.152.738 LAURO MARCELO GUEDES MONTEIRO": "244",
            "47.906.182 JEFFERSON LENO DA CONCEICAO": "901",
            "37.981.071 RAFAEL RIBEIRO FERREIRA": "799",
            "52.058.411 LIVIO DA SILVA CARDEAL": "557",
            "54.567.741 JOAO VICTOR AMORIM FREITAS": "221",
            "58.583.285 SHIRLEY DE TORRES BANDEIRA": "501",
            "59.364.239 JOAO PEDRO SIMOES ALVES NASCIMENTO": "800",
            "COLIBRI GRAFICA E SINALIZACAO LTDA": "946",
            "ESTEVAO HENRIQUE SANTIAGO DE OLIVEIRA 14053483484": "801",
            "57.891.175 PAULA LUANNA GONCALVES AZEVEDO": "803",
            "65.866.149 MARIANA MAFRA BARRETO": "894",
            "JM DE OLIVEIRA FILHO LTDA": "811",
            "BRUNO CALDAS NOBLAT 70998806404": "815",
            "41.361.570 SUELEN KARINE DA SILVA ROCHA": "817",
            "SPORTS WEB BRASIL - CONTEUDOS DIGITAIS LTDA.": "568",
            "47.536.085 RAFAEL FIGUEIREDO ANDRADE": "896",
            "Z3 PROPAGANDA LTDA": "162",
            "GYNO DANIEL BEZERRA SILVA": "87",
            "FRANCO, CABRAL & SILVA ADVOGADOS": "954",
            "59.446.331 LEONARDO DE MELO VERAS": "895",
            "59.515.060 ISRAEL CESAR PAIVA DE SOUZA": "824",
            "ANDRE LUIZ ALVES RIBEIRO": "96",
            "60.192.699 LEONARDO AMORIM DE ARAUJO": "580",
            "61.071.595 MACAS VASCONCELOS VIANA": "826",
            "VP SERVICOS EMPRESARIAIS LTDA": "829",
            "VISIONARY TECH LTDA": "656",
            "65.055.563 THIAGO WILLIAMS BEZERRA ZILLINGER": "650",
            "66.449.928 LARYSSA CAMILA CAMPOS BEZERRA": "955",
            "60.606.033 GEINNY STEPHANE ATAIDE LIMA": "442",
            "61.761.809 GEAN AFONSO SILVA DE CARVALHO": "507",
            "IVY PRODUCOES ARTISTICAS LTDA": "253",
            "ISR PRODUCOES E EVENTOS LTDA": "585",
            "53.037.512 MARCUS VINICIUS GUEDES AMBROZIO": "399",
            "PLAY ONLINE MULTIMARKETING LTDA": "125",
            "CAIKE BONFIM ALVES PRODUCOES E MARKETING": "848",
            "MOVEUP MEDIA BRAZIL LTDA": "252",
            "SIMOES DIVULGACOES LTDA": "88",
            "KR3W NETWORK LTDA": "859",
            "APX ENGAGE - DIGITAL SOLUTIONS LTDA": "144",
            "SHORT CODE AUTOMACAO DE SERVICOS LTDA": "95"
        }
    }
}

if 'empresas_db' not in st.session_state:
    st.session_state['empresas_db'] = BANCO_DE_DADOS_EMPRESAS_INICIAL.copy()

# Base ativa Fixa/Selecionável
empresa_selecionada = "PIXBET SOLUCOES TECNOLOGICAS LTDA"
config_atual = st.session_state['empresas_db'][empresa_selecionada]

with st.sidebar:
    st.header("⚙️ Parâmetros de Conciliação")
    ignorar_data = st.checkbox("Ignorar Limite de Datas", value=True)
    tolerancia_dias = 99999 if ignorar_data else st.slider("Tolerância de Dias:", 0, 30, 5)
    
    st.divider()
    ignorar_txt = st.text_area("Ignorar no Extrato:", "SALDO INICIAL, SALDO FINAL, TRANSFERENCIA INTERNA")
    termos_ignorar = [t.strip().upper() for t in ignorar_txt.split(',')]

# --- UPLOAD DE ARQUIVOS ---
c1, c2 = st.columns(2)
with c1: excel_file = st.file_uploader("📂 Upload Planilha de Entradas (Fiscal)", type=["xlsx", "xls", "csv"])
with c2: receipt_files = st.file_uploader("📄 Upload Extrato Bancário (PDF/Excel)", type=["pdf", "xlsx", "xls", "csv"], accept_multiple_files=True)

if excel_file and receipt_files:
    # Processa o Fiscal (Excel de Entradas)
    try:
        if excel_file.name.endswith('.csv'):
            df_dom = pd.read_csv(excel_file, sep=',', encoding='utf-8-sig')
            if len(df_dom.columns) < 5:
                excel_file.seek(0)
                df_dom = pd.read_csv(excel_file, sep=';')
        else:
            df_dom = pd.read_excel(excel_file)
    except Exception as e:
        st.error(f"Erro ao ler fiscal: {e}")
        st.stop()

    # Extrai o extrato bancário
    extrato_bancario = []
    for f in receipt_files:
        extrato_bancario.extend(extrair_dados_arquivo(f, config_atual["bancos"], config_atual["impostos"], False, termos_ignorar))

    # --- MOTOR DE CONCILIAÇÃO IA INTELIGENTE ---
    matriz_final = []
    ids_extrato_usados = set()
    
    # Normalização e Limpeza do arquivo fiscal
    df_dom.columns = [str(c).strip() for c in df_dom.columns]
    
    # Identifica colunas chave por Regex defensivo
    col_data = next((c for c in df_dom.columns if re.search(r'data', c, re.IGNORECASE)), None)
    col_valor = next((c for c in df_dom.columns if re.search(r'valor contábil|valor', c, re.IGNORECASE)), None)
    col_forn = next((c for c in df_dom.columns if re.search(r'fornecedor|nome', c, re.IGNORECASE)), None)
    col_nota = next((c for c in df_dom.columns if re.search(r'nota|documento', c, re.IGNORECASE)), None)
    col_codigo = next((c for c in df_dom.columns if re.search(r'código|cod', c, re.IGNORECASE)), None)

    # 1ª PASSAGEM: Cruzamento de Linhas do Fiscal (Misto ou Só Nota)
    for idx, row in df_dom.iterrows():
        # Ignora linhas de cabeçalho ou totais poluídas
        if pd.isna(row.get(col_forn)) or "TOTAL" in str(row.get(col_forn)).upper() or "ACOMPANHAMENTO" in str(row.get(col_forn)).upper():
            continue
            
        forn_fiscal = str(row.get(col_forn)).strip()
        forn_fiscal_clean = normalizar_espacos(forn_fiscal)
        
        val_fiscal_bruto = abs(limpar_valor(row.get(col_valor, 0)))
        data_fiscal_obj = converter_data_dominio(row.get(col_data))
        nota_fiscal = str(row.get(col_nota, "-")).split('.')[0]
        cod_forn = str(row.get(col_codigo, "-")).split('.')[0]

        # Tratamento de Deduções / Impostos da Linha para achar o Líquido
        # Se houver colunas extras de impostos na linha seguinte ou colunas do dataframe:
        val_irrf = abs(limpar_valor(row.get('Valor', 0))) if 'Valor' in df_dom.columns else 0.0
        val_liquido_esperado = val_fiscal_bruto - val_irrf

        match_banco = None
        
        # Procura par no extrato bancário
        for i, trans in enumerate(extrato_bancario):
            if i in ids_extrato_usados: continue
            
            # Validação se o nome do fornecedor bate por proximidade ou substring
            nome_banco_clean = normalizar_espacos(trans['Fav'])
            nome_bate = (forn_fiscal_clean[:10] in nome_banco_clean) or (nome_banco_clean[:10] in forn_fiscal_clean)
            
            # Validação de Datas
            try:
                data_banco_obj = datetime.strptime(trans['Data'], '%d/%m/%Y').date()
                dias_dif = abs((data_fiscal_obj - data_banco_obj).days) if data_fiscal_obj else 999
            except: dias_dif = 999
            
            data_valida = dias_dif <= tolerancia_dias

            # Condição de Match Inteligente (Considera Bruto, Líquido ou erro de nota R$ 0 da KR3W)
            if data_valida:
                if val_fiscal_bruto == 0.0 and nome_bate and not trans['Is_Credito']:
                    # Caso Especial: Nota Fiscal Zerada (Ex: KR3W Network)
                    match_banco = trans
                    ids_extrato_usados.add(i)
                    break
                elif abs(val_fiscal_bruto - trans['Total']) < 0.1:
                    # Caso Padrão: Valor Bruto bate exato com o banco
                    match_banco = trans
                    ids_extrato_usados.add(i)
                    break
                elif abs(val_liquido_esperado - trans['Total']) < 0.1 and val_irrf > 0:
                    # Caso com Retenção: O Banco pagou o valor líquido deduzido
                    match_banco = trans
                    ids_extrato_usados.add(i)
                    break

        # Consolidação da Linha na Matriz Final
        if match_banco:
            matriz_final.append({
                'Data de Ref.': data_fiscal_obj.strftime('%d/%m/%Y') if data_fiscal_obj else trans['Data'],
                'Tipo de Lançamento': 'Misto (NF + Pagamento)' if not match_banco['Is_Credito'] else 'Misto (NF + Recebimento)',
                'Nº Nota / Doc': nota_fiscal,
                'Cód. Forn.': cod_forn,
                'Participante / Favorecido': forn_fiscal,
                'Valor Nota (R$)': val_fiscal_bruto,
                'Valor Saída (R$)': match_banco['Total'] if not match_banco['Is_Credito'] else 0.0,
                'Valor Entrada (R$)': match_banco['Total'] if match_banco['Is_Credito'] else 0.0,
                'Status / Classificação Contábil': '✅ CONCILIADO' if val_fiscal_bruto > 0 else '⚠️ CONCILIADO COM DIVERGÊNCIA VALOR FICAL'
            })
        else:
            # Não achou no extrato bancário = Ficou apenas no Fiscal
            matriz_final.append({
                'Data de Ref.': data_fiscal_obj.strftime('%d/%m/%Y') if data_fiscal_obj else '-',
                'Tipo de Lançamento': 'Nota Fiscal',
                'Nº Nota / Doc': nota_fiscal,
                'Cód. Forn.': cod_forn,
                'Participante / Favorecido': forn_fiscal,
                'Valor Nota (R$)': val_fiscal_bruto,
                'Valor Saída (R$)': 0.0,
                'Valor Entrada (R$)': 0.0,
                'Status / Classificação Contábil': '❌ Só no Domínio (Sem Saída Bancária)'
            })

    # 2ª PASSAGEM: Adiciona tudo o que sobrou no extrato (Movimentos sem Nota Fiscal)
    for i, trans in enumerate(extrato_bancario):
        if i not in ids_extrato_usados:
            # Tenta mapear o código do fornecedor pelo banco de dados se o nome bater
            cod_sugerido = "-"
            for k, v in config_atual["fornecedores"].items():
                if normalizar_espacos(k)[:12] in normalizar_espacos(trans['Fav']):
                    cod_sugerido = v
                    break

            matriz_final.append({
                'Data de Ref.': trans['Data'],
                'Tipo de Lançamento': 'Recebimento' if trans['Is_Credito'] else 'Pagamento',
                'Nº Nota / Doc': '-',
                'Cód. Forn.': cod_sugerido,
                'Participante / Favorecido': trans['Fav'],
                'Valor Nota (R$)': 0.0,
                'Valor Saída (R$)': trans['Total'] if not trans['Is_Credito'] else 0.0,
                'Valor Entrada (R$)': trans['Total'] if trans['Is_Credito'] else 0.0,
                'Status / Classificação Contábil': '⚠️ Só no Extrato (Falta Emitir/Lançar Nota)'
            })

    # Transforma em Dataframe e Organiza Exibição
    df_resultado = pd.DataFrame(matriz_final)
    
    # Formatação Estética para a Tela do Streamlit
    df_display = df_resultado.copy()
    for col in ['Valor Nota (R$)', 'Valor Saída (R$)', 'Valor Entrada (R$)']:
        df_display[col] = df_display[col].apply(formatar_moeda)

    st.success("🏁 Processo de conciliação executado com sucesso e sem omissões!")
    st.dataframe(df_display, use_container_width=True)

    # --- BOTÃO DE DOWNLOAD DIRETO PARA EXCEL/CSV ---
    csv = df_resultado.to_csv(index=False, sep=';').encode('utf-8-sig')
    st.download_button(
        label="📥 Baixar Matriz Completa para Excel (.CSV)",
        data=csv,
        file_name=f"Conciliacao_Unificada_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv"
    )
