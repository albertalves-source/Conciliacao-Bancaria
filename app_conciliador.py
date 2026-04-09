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
        if num > 10000: # Excel Serial Date
            return pd.to_datetime(num, unit='D', origin='1899-12-30').date()
    except: pass
    try: 
        return pd.to_datetime(data_obj, dayfirst=True).date()
    except:
        match = re.search(r'(\d{2}/\d{2}/\d{4})', str(data_obj))
        if match: return datetime.strptime(match.group(1), '%d/%m/%Y').date()
        return None

def limpar_nome_contabil(nome):
    """Limpeza cirúrgica: remove IDs técnicos sem destruir nomes de empresas."""
    if not nome or str(nome).lower() in ["n/a", "nan", "0", "none"]: return ""
    
    n = str(nome).upper()
    
    # 1. Remove UUIDs (IDs com hífens como 0D857627-F469-...)
    n = re.sub(r'[A-Z0-9]{8}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{12}', '', n)
    
    # 2. Remove fragmentos alfanuméricos (palavras que misturam letras e números, ex: F469, 998D)
    # Protege palavras que são apenas letras (como INTERNATIONAL ou SELECT)
    n = re.sub(r'\b(?=[A-Z]*[0-9])(?=[0-9]*[A-Z])[A-Z0-9]+\b', '', n)
    
    # 3. Remove números longos isolados (IDs bancários, códigos de barras)
    n = re.sub(r'\b\d{6,}\b', '', n)
    n = re.sub(r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{3}\.\d{3}\.\d{3}-\d{2}', '', n)
    
    # 4. Termos técnicos de extratos bancários
    termos_lixo = [
        "PIX ENVIADO PARA", "PIX RECEBIDO", "TRANSFERÊNCIA ENVIADA PARA", "TRANSFERÊNCIA RECEBIDA",
        "PAGADOR", "BENEFICIARIO", "RAZAO SOCIAL", "FAVORECIDO", "VALOR PAGO", "DATA DO", "PAGAMENTO",
        "BOLETO", "PAYMENT", "SALDO DISPONÍVEL", "CONNECTPSP", "DESENVOLVEDORA", "R\$", "DE R\$", 
        "INSTITUICAO", "AUTENTICACAO", "COMPROVANTE", "OPERATIONS", "LTDA", "S.A.", "S/A", "SA", "ME"
    ]
    for t in termos_lixo:
        n = re.sub(r'\b' + t + r'\b', '', n)
    
    # 5. Limpeza final de pontuação
    n = re.sub(r'[:\-,\(\)_]', ' ', n)
    
    # Retorna apenas o que sobrou com mais de 2 letras
    palavras = [w for w in n.split() if len(w) > 2]
    return ' '.join(palavras).strip()

def extrair_dados_arquivo(file, mapa_bancos, mapa_imp, usar_ia):
    transacoes = []
    banco_base = ""
    for b_key in mapa_bancos.keys():
        if b_key in file.name.upper(): banco_base = b_key; break

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
                    for linha in texto_pagina.split('\n'):
                        # Filtro crucial: Ignora linhas de saldo e cabeçalho do extrato
                        if any(x in linha.upper() for x in ["SALDO", "RESUMO", "DISPONÍVEL", "VALOR TOTAL"]): continue
                        
                        data_match = re.search(r'(\d{2}/\d{2}/\d{4})', linha)
                        valor_match = re.findall(r'(\d[\d\.]*,\d{2})', linha)
                        
                        if data_match and valor_match:
                            desc_bruta = linha.replace(data_match.group(1), "")
                            for v_txt in valor_match: desc_bruta = desc_bruta.replace(v_txt, "")
                            
                            # Cód. Receita: Só captura se for explicitamente um imposto do plano
                            cod_found = ""
                            codes = re.findall(r'\b(\d{4})\b', linha)
                            for c in codes:
                                if c in mapa_imp: cod_found = c; break
                            
                            for v_txt in valor_match:
                                val = limpar_valor(v_txt)
                                if val > 0:
                                    transacoes.append({
                                        'Data': [data_match.group(1)], 'Total': val,
                                        'Cod': cod_found, 'Fav': limpar_nome_contabil(desc_bruta), 
                                        'Banc': banco_base, 'IA': False, 'Arq': file.name,
                                        'Principal': val, 'Multa': 0.0, 'Juros': 0.0
                                    })
                # Fallback para comprovantes simples
                if not transacoes:
                    texto_completo = "\n".join([p.extract_text() or "" for p in pdf.pages])
                    rec = re.search(r'(?:RECEITA|CODIGO|RECEITA:)\s*(\d{4})', texto_completo, re.IGNORECASE)
                    datas = list(set(re.findall(r'(\d{2}/\d{2}/\d{4})', texto_completo)))
                    valores = re.findall(r'(\d[\d\.]*,\d{2})', texto_completo)
                    if datas and valores:
                        v_f = limpar_valor(valores[-1])
                        transacoes.append({
                            'Data': datas, 'Total': v_f, 'Cod': rec.group(1) if rec else "",
                            'Banc': banco_base, 'Fav': "COMPROVANTE",
                            'IA': False, 'Arq': file.name, 'Principal': v_f, 'Multa': 0.0, 'Juros': 0.0
                        })
        except: pass
        
    return transacoes

# --- BIBLIOTECA PADRÃO ---
DEFAULTS_IMPOSTOS = {'0561': {'n': 'IRRF s/ Salários', 'c': '2105'}, '2172': {'n': 'COFINS Faturamento', 'c': '2108'}, '8109': {'n': 'PIS Faturamento', 'c': '2110'}}
DEFAULTS_BANCOS = {'ITAU': {'n': 'Itaú', 'r': '10'}, 'BRAD': {'n': 'Bradesco', 'r': '20'}, 'SANTANDER': {'n': 'Santander', 'r': '30'}, 'BRASIL': {'n': 'B. Brasil', 'r': '01'}, 'DELFIN': {'n': 'Delfinance', 'r': '99'}}

# --- INTERFACE ---
st.title("🏦 Conciliador Contábil IA V18.0")
st.markdown("Filtro Industrial de IDs: O robô agora prioriza os nomes que você digitou no Excel.")

with st.sidebar:
    st.header("⚙️ Parâmetros")
    tolerancia_dias = st.slider("Tolerância de Datas (dias):", 0, 10, 3)
    st.divider()
    st.header("📋 Plano de Contas")
    mapa_imp = {cod: {'conta': st.text_input(f"{info['n']}", info['c']), 'nome': info['n']} for cod, info in DEFAULTS_IMPOSTOS.items()}
    mapa_bancos = {k: {'reduzido': st.text_input(f"Cod. {v['n']}", v['r']), 'nome': v['n']} for k, v in DEFAULTS_BANCOS.items()}

c1, c2 = st.columns(2)
with c1: excel_file = st.file_uploader("📂 Relatório Domínio", type=["xlsx", "xls", "csv"])
with c2: receipt_files = st.file_uploader("📄 PDFs/Extratos", type=["pdf", "png", "jpg"], accept_multiple_files=True)

if excel_file and receipt_files:
    try:
        df_dom = pd.read_excel(excel_file) if not excel_file.name.endswith('.csv') else pd.read_csv(excel_file, sep=None, engine='python')
        df_dom.columns = [str(c).replace('\n', ' ').strip() for c in df_dom.columns]
        c_d = next((c for c in df_dom.columns if "data" in c.lower()), None)
        c_v = next((c for c in df_dom.columns if "valor" in c.lower() and "cont" in c.lower()), next((c for c in df_dom.columns if "valor" in c.lower() or "vlr" in c.lower()), None))
        c_cli = next((c for c in df_dom.columns if any(x in c.lower() for x in ["fornecedor", "cliente", "nome"])), "Fornecedor")
        df_dom = df_dom.reset_index(drop=True)
    except Exception as e:
        st.error(f"Erro ao ler planilha: {e}"); st.stop()

    todas_transacoes_pdf = []
    for f in receipt_files:
        with st.spinner(f"Lendo {f.name}..."):
            todas_transacoes_pdf.extend(extrair_dados_arquivo(f, mapa_bancos, mapa_imp, True))

    rows, ids_pdf_usados = [], set()
    for idx, l in df_dom.iterrows():
        v_ex = limpar_valor(l[c_v])
        d_ex_obj = converter_data_dominio(l[c_d])
        if v_ex == 0 or d_ex_obj is None: continue 
        
        match_found = False
        for i, doc in enumerate(todas_transacoes_pdf):
            if i in ids_pdf_usados: continue
            for d_pdf_str in doc['Data']:
                try:
                    d_pdf_obj = datetime.strptime(d_pdf_str, '%d/%m/%Y').date()
                    if abs(v_ex - doc['Total']) < 0.05 and abs((d_ex_obj - d_pdf_obj).days) <= tolerancia_dias:
                        regra_imp = mapa_imp.get(doc['Cod'], {'conta': '9999', 'nome': '-'})
                        b_inf = next((v for k, v in mapa_bancos.items() if k in str(doc['Banc']).upper()), {'nome': 'BANCO', 'reduzido': '99'})
                        
                        # MASTER FIX: Se conciliou, o nome do Favorecido DEVE vir do EXCEL (o oficial)
                        fav_final = str(l.get(c_cli, '')).upper()
                        if fav_final == "NAN" or not fav_final: fav_final = doc['Fav']

                        rows.append({
                            'Status': '✅ CONCILIADO', 'Data Excel': d_ex_obj.strftime('%d/%m/%Y'), 'Valor Total': v_ex,
                            'Imposto': regra_imp['nome'], 'Favorecido': fav_final, 'Data PDF': d_pdf_obj.strftime('%d/%m/%Y'),
                            'Banco': b_inf['nome'], 'Débito': regra_imp['conta'], 'Crédito': b_inf['reduzido'], 
                            'Principal': doc.get('Principal', v_ex), 'Multa': doc.get('Multa', 0.0), 'Juros': doc.get('Juros', 0.0),
                            'Cód. Receita': doc['Cod'], 'Arquivo': doc['Arq']
                        })
                        ids_pdf_usados.add(i); match_found = True; break
                except: continue
            if match_found: break
        if not match_found:
            rows.append({'Status': '❌ FALTA PDF', 'Data Excel': d_ex_obj.strftime('%d/%m/%Y'), 'Valor Total': v_ex, 'Favorecido': str(l.get(c_cli, '')).upper()})

    for i, doc in enumerate(todas_transacoes_pdf):
        if i not in ids_pdf_usados:
            b_inf = next((v for k, v in mapa_bancos.items() if k in str(doc['Banc']).upper()), {'nome': 'BANCO', 'reduzido': '99'})
            rows.append({'Status': '⚠️ SÓ NO PDF', 'Data PDF': doc['Data'][0], 'Valor Total': doc['Total'], 'Imposto': mapa_imp.get(doc['Cod'], {'nome':'-'})['nome'], 'Favorecido': doc['Fav'], 'Banco': b_inf['nome'], 'Arquivo': doc['Arq']})

    res_df = pd.DataFrame(rows).fillna("-")
    st.subheader("📋 Relatório de Conciliação")
    disp = res_df.copy()
    col_order = ['Status', 'Data Excel', 'Valor Total', 'Imposto', 'Favorecido', 'Data PDF', 'Banco', 'Débito', 'Crédito', 'Principal', 'Multa', 'Juros', 'Cód. Receita', 'Arquivo']
    disp = disp[[c for c in col_order if c in disp.columns]]
    for col in ['Valor Total', 'Principal', 'Multa', 'Juros']:
        if col in disp.columns: disp[col] = disp[col].apply(formatar_moeda)

    def color_status(val):
        color = 'rgba(46, 204, 113, 0.08)' if val == '✅ CONCILIADO' else 'rgba(231, 76, 60, 0.08)' if val == '❌ FALTA PDF' else 'rgba(241, 196, 15, 0.08)'
        return f'background-color: {color}'

    styled = disp.style.map(color_status, subset=['Status']) if hasattr(disp.style, 'map') else disp.style.applymap(color_status, subset=['Status'])
    st.dataframe(styled, use_container_width=True)
    
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='xlsxwriter') as wr: res_df.to_excel(wr, index=False)
    st.download_button("📥 Baixar Excel Industrial", out.getvalue(), "conciliacao_industrial.xlsx")
