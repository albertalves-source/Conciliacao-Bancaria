import streamlit as st
import pandas as pd
import re
import io
import warnings
import requests
import json
import time
import base64
from datetime import datetime

# Tenta importar bibliotecas extras de forma segura
try:
    import pdfplumber
except ImportError:
    st.error("Erro: A biblioteca 'pdfplumber' não foi encontrada. Verifique o seu requirements.txt.")

# Configurações de Página
st.set_page_config(page_title="Portal de Conciliação IA - Extratos", layout="wide", page_icon="🏦")
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
    # Remove símbolos e garante formato decimal
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
            return pd.to_datetime(num, unit='D', origin='1899-12-30').strftime('%d/%m/%Y')
    except: pass
    try: 
        return pd.to_datetime(data_obj, dayfirst=True).strftime('%d/%m/%Y')
    except:
        match = re.search(r'(\d{2}/\d{2}/\d{4})', str(data_obj))
        return match.group(1) if match else None

def limpar_nome_contabil(nome):
    if not nome or str(nome).lower() in ["n/a", "nan", "0", "none"]: return ""
    nome = re.sub(r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{3}\.\d{3}\.\d{3}-\d{2}', '', str(nome))
    termos = ["DATA DO", "PAGAMENTO", "BENEFICIARIO", "RAZAO SOCIAL", "NOME", "FAVORECIDO"]
    for t in termos: nome = re.sub(t, '', nome, flags=re.IGNORECASE)
    return nome.replace(':', '').strip().upper()

def extrair_dados_arquivo(file, mapa_bancos, usar_ia):
    """Extrai uma LISTA de transações de um PDF (suporta Extratos e Comprovantes)."""
    transacoes = []
    
    if file.name.lower().endswith(".pdf"):
        try:
            with pdfplumber.open(file) as pdf:
                for page in pdf.pages:
                    texto_pagina = page.extract_text()
                    if not texto_pagina: continue
                    
                    linhas = texto_pagina.split('\n')
                    for linha in linhas:
                        # Busca data e valor na mesma linha (padrão de extrato)
                        data_match = re.search(r'(\d{2}/\d{2}/\d{4})', linha)
                        valor_match = re.findall(r'(\d[\d\.]*,\d{2})', linha)
                        
                        if data_match and valor_match:
                            for v_txt in valor_match:
                                val = limpar_valor(v_txt)
                                if val > 0:
                                    transacoes.append({
                                        'Data': [data_match.group(1)],
                                        'Total': val,
                                        'Cod': "", 'Fav': "EXTRATO BANCARIO", 'Banc': "", 'IA': False, 'Arq': file.name
                                    })
                
                # Se for um comprovante único (não achou linhas de extrato)
                if not transacoes:
                    texto_completo = "\n".join([p.extract_text() or "" for p in pdf.pages])
                    rec = re.search(r'(?:RECEITA|CODIGO|RECEITA:)\s*(\d{4})', texto_completo, re.IGNORECASE)
                    banco_detectado = ""
                    for termo in mapa_bancos.keys():
                        if termo in texto_completo.upper(): banco_detectado = termo; break
                    
                    datas = list(set(re.findall(r'(\d{2}/\d{2}/\d{4})', texto_completo)))
                    valores = re.findall(r'(\d[\d\.]*,\d{2})', texto_completo)
                    if datas and valores:
                        transacoes.append({
                            'Data': datas,
                            'Total': limpar_valor(valores[-1]),
                            'Cod': rec.group(1) if rec else "",
                            'Banc': banco_detectado,
                            'Fav': "COMPROVANTE PDF",
                            'IA': False, 'Arq': file.name
                        })
        except: pass

    # Se ainda estiver vazio e for imagem ou PDF difícil, usa IA
    if not transacoes and usar_ia:
        prompt = "Extraia as transações deste documento em JSON: [{'data': 'DD/MM/AAAA', 'valor_total': 0.0, 'favorecido': 'Nome'}]"
        mime = "application/pdf" if file.name.lower().endswith(".pdf") else "image/jpeg"
        base64_data = base64.b64encode(file.getvalue()).decode("utf-8")
        ia_res = processar_ia_generativa(prompt, base64_data, mime)
        if isinstance(ia_res, list):
            for item in ia_res:
                transacoes.append({
                    'Data': [item.get('data', "")],
                    'Total': item.get('valor_total', 0.0),
                    'Fav': item.get('favorecido', ""),
                    'Cod': "", 'Banc': "", 'IA': True, 'Arq': file.name
                })
                
    return transacoes

# --- BIBLIOTECA PADRÃO ---
DEFAULTS_IMPOSTOS = {'0561': {'n': 'IRRF s/ Salários', 'c': '2105'}, '2172': {'n': 'COFINS Faturamento', 'c': '2108'}, '8109': {'n': 'PIS Faturamento', 'c': '2110'}}
DEFAULTS_BANCOS = {'ITAU': {'n': 'Itaú', 'r': '10'}, 'BRAD': {'n': 'Bradesco', 'r': '20'}, 'SANTANDER': {'n': 'Santander', 'r': '30'}, 'BRASIL': {'n': 'B. Brasil', 'r': '01'}, 'DELFIN': {'n': 'Delfinance', 'r': '99'}}

# --- INTERFACE ---
st.title("🏦 Portal de Conciliação Contábil V9.0")
st.markdown("Otimizado para leitura de **Extratos Bancários** e Relatórios Domínio.")

with st.sidebar:
    st.header("⚙️ Configurações")
    ia_on = st.toggle("Ativar IA (Para PDFs complexos)", value=True)
    mapa_imp = {cod: {'conta': st.text_input(f"{info['n']}", info['c']), 'nome': info['n']} for cod, info in DEFAULTS_IMPOSTOS.items()}
    mapa_bancos = {k: {'reduzido': st.text_input(f"Cod. {v['n']}", v['r']), 'nome': v['n']} for k, v in DEFAULTS_BANCOS.items()}

c1, c2 = st.columns(2)
with c1: excel_file = st.file_uploader("📂 Relatório Domínio (Excel/CSV)", type=["xlsx", "xls", "csv"])
with c2: receipt_files = st.file_uploader("📄 Extratos/Comprovantes (PDF/Imagens)", type=["pdf", "png", "jpg"], accept_multiple_files=True)

if excel_file and receipt_files:
    try:
        df_dom = pd.read_excel(excel_file) if not excel_file.name.endswith('.csv') else pd.read_csv(excel_file, sep=None, engine='python')
        df_dom.columns = [str(c).replace('\n', ' ').strip() for c in df_dom.columns]
        c_d = next((c for c in df_dom.columns if "data" in c.lower()), None)
        c_v = next((c for c in df_dom.columns if "valor" in c.lower() and "cont" in c.lower()), next((c for c in df_dom.columns if "valor" in c.lower() or "vlr" in c.lower()), None))
    except Exception as e:
        st.error(f"Erro ao ler planilha: {e}"); st.stop()

    # Processar todos os ficheiros PDF e extrair TODAS as transações
    todas_transacoes_pdf = []
    for f in receipt_files:
        with st.spinner(f"Processando {f.name}..."):
            itens = extrair_dados_arquivo(f, mapa_bancos, ia_on)
            todas_transacoes_pdf.extend(itens)

    rows, ids_pdf_usados = [], set()
    
    # Cruzamento Excel -> PDFs
    for idx, l in df_dom.iterrows():
        v_ex = limpar_valor(l[c_v])
        d_ex = converter_data_dominio(l[c_d])
        
        if v_ex == 0 or d_ex is None: continue # Ignora linhas inválidas
        
        match = False
        for i, doc in enumerate(todas_transacoes_pdf):
            # Match por Valor e Data
            if abs(v_ex - doc['Total']) < 0.05 and d_ex in doc['Data']:
                i_inf = mapa_imp.get(doc['Cod'], {'conta': '9999', 'nome': doc['Fav']})
                b_inf = next((v for k, v in mapa_bancos.items() if k in str(doc['Banc']).upper() or k in doc['Arq'].upper()), {'nome': 'BANCO', 'reduzido': '99'})
                
                rows.append({
                    'Status': '✅ CONCILIADO', 'Data': d_ex, 'Valor Total': v_ex,
                    'Imposto/Fav': i_inf['nome'], 'Débito': i_inf['conta'], 'Crédito': b_inf['reduzido'], 'Arquivo': doc['Arq']
                })
                ids_pdf_usados.add(i)
                match = True
                break
        
        if not match:
            rows.append({'Status': '❌ FALTA PDF', 'Data': d_ex, 'Valor Total': v_ex, 'Imposto/Fav': l.get('Fornecedor', '-')})

    # Adicionar transações que estão no PDF mas não no Excel
    for i, doc in enumerate(todas_transacoes_pdf):
        if i not in ids_pdf_usados:
            rows.append({
                'Status': '⚠️ SÓ NO PDF', 'Data': doc['Data'][0] if doc['Data'] else "-", 'Valor Total': doc['Total'],
                'Imposto/Fav': doc['Fav'], 'Arquivo': doc['Arq']
            })

    res_df = pd.DataFrame(rows)
    
    # Formatação Visual
    def color_status(val):
        if val == '✅ CONCILIADO': return 'background-color: rgba(46, 204, 113, 0.1)'
        if val == '❌ FALTA PDF': return 'background-color: rgba(231, 76, 60, 0.1)'
        return 'background-color: rgba(241, 196, 15, 0.1)'

    st.subheader("📋 Relatório de Conciliação")
    disp = res_df.copy()
    disp['Valor Total'] = disp['Valor Total'].apply(formatar_moeda)
    st.dataframe(disp.style.applymap(color_status, subset=['Status']), use_container_width=True)
    
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='xlsxwriter') as wr: res_df.to_excel(wr, index=False)
    st.download_button("📥 Baixar Relatório", out.getvalue(), "conciliacao_extrato.xlsx")
