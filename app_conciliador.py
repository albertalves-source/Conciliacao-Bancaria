import streamlit as st
import pandas as pd
import re
import io
import warnings
import requests
import json
import time

# Tenta importar bibliotecas extras de forma segura
try:
    import pdfplumber
except ImportError:
    st.error("Erro: A biblioteca 'pdfplumber' não foi encontrada. Verifique o seu requirements.txt.")

# Configurações de Página
st.set_page_config(page_title="Portal de Conciliação Contábil IA", layout="wide", page_icon="🏦")
warnings.filterwarnings("ignore")

# --- CONFIGURAÇÃO DA IA (GEMINI) ---
api_key = st.secrets.get("GEMINI_API_KEY", "")

def consultar_ia(texto_pdf):
    if not api_key: return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={api_key}"
    
    prompt = f"""
    Você é um assistente contábil. Analise o texto do comprovante e extraia JSON:
    {{
      "imposto_nome": "Nome do Imposto",
      "codigo_receita": "4 dígitos",
      "banco_nome": "Nome do Banco"
    }}
    Texto: {texto_pdf[:1500]}
    """
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json"}
    }
    
    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code == 200:
            res_json = response.json()
            content = res_json['candidates'][0]['content']['parts'][0]['text']
            return json.loads(content)
    except:
        return None
    return None

# --- ESTILIZAÇÃO E PERFUMARIA ---
st.markdown("""
    <style>
    [data-testid="stMetricValue"] { font-size: 26px; font-weight: 700; }
    div[data-testid="metric-container"] {
        border: 1px solid rgba(128, 128, 128, 0.2);
        padding: 20px;
        border-radius: 15px;
        background-color: rgba(128, 128, 128, 0.03);
    }
    .stDataFrame { border-radius: 12px; }
    </style>
    """, unsafe_allow_html=True)

# --- FUNÇÕES DE APOIO ---
def formatar_moeda(v):
    """Formata valor para padrão R$ 1.234,56."""
    try:
        val = float(v)
        if val == 0: return "-"
        return f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return "-"

def limpar_valor(v):
    if pd.isna(v): return 0.0
    v_str = str(v).replace('R$', '').replace('$', '').replace(' ', '').strip()
    if ',' in v_str and '.' in v_str: v_str = v_str.replace('.', '').replace(',', '.')
    elif ',' in v_str: v_str = v_str.replace(',', '.')
    try: return float(v_str)
    except: return 0.0

def padronizar_data(data_obj):
    try: return pd.to_datetime(data_obj, dayfirst=True).strftime('%d/%m/%Y')
    except:
        match = re.search(r'(\d{2}/\d{2}/\d{4})', str(data_obj))
        return match.group(1) if match else str(data_obj)

def limpar_nome_contabil(nome):
    if not nome or str(nome).lower() in ["n/a", "nan", "0", "none"]: return ""
    nome = re.sub(r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{3}\.\d{3}\.\d{3}-\d{2}', '', str(nome))
    termos = ["DATA DO", "PAGAMENTO", "BENEFICIARIO", "RAZAO SOCIAL", "NOME", "FAVORECIDO"]
    for t in termos: nome = re.sub(t, '', nome, flags=re.IGNORECASE)
    return nome.replace(':', '').strip().upper()

def extrair_detalhes_pdf(file_pdf, mapa_bancos, usar_ia):
    det = {'Data': [], 'Total': 0.0, 'Principal': 0.0, 'Multa': 0.0, 'Juros': 0.0, 
           'Cod': "", 'Fav': "", 'Banc': "", 'IA': False, 'IA_Imp': ""}
    try:
        with pdfplumber.open(file_pdf) as pdf:
            texto = pdf.pages[0].extract_text()
            if not texto: return None
            texto_upper = texto.upper()
            det['Data'] = list(set(re.findall(r'(\d{2}/\d{2}/\d{4})', texto)))
            
            rec = re.search(r'(?:RECEITA|CODIGO|RECEITA:)\s*(\d{4})', texto, re.IGNORECASE)
            if rec: det['Cod'] = rec.group(1)
            
            for termo, info in mapa_bancos.items():
                if termo in texto_upper:
                    det['Banc'] = termo
                    break
            
            vals = re.findall(r'(\d[\d\.]*,\d{2})', texto)
            if vals:
                det['Total'] = limpar_valor(vals[-1])
                if len(vals) >= 4:
                    det['Principal'] = limpar_valor(vals[-4])
                    det['Multa'] = limpar_valor(vals[-3])
                    det['Juros'] = limpar_valor(vals[-2])
                else: det['Principal'] = det['Total']
            
            if usar_ia and api_key and (not det['Cod'] or not det['Banc']):
                ia_res = consultar_ia(texto)
                if ia_res:
                    det['Cod'] = ia_res.get('codigo_receita', det['Cod'])
                    det['IA_Imp'] = ia_res.get('imposto_nome', "")
                    det['IA'] = True

            for linha in texto.split('\n'):
                if any(x in linha.upper() for x in ["BENEFICIARIO", "RAZAO SOCIAL", "NOME", "FAVORECIDO"]):
                    det['Fav'] = limpar_nome_contabil(linha.split(':')[-1] if ':' in linha else linha)
                    break
    except: pass
    return det

# --- BIBLIOTECA PADRÃO ---
DEFAULTS_IMPOSTOS = {
    '0561': {'n': 'IRRF s/ Salários', 'c': '2105'},
    '2172': {'n': 'COFINS Faturamento', 'c': '2108'},
    '8109': {'n': 'PIS Faturamento', 'c': '2110'},
    '5952': {'n': 'CSRF Retenções', 'c': '2115'},
}

DEFAULTS_BANCOS = {
    'ITAU': {'n': 'Itaú', 'r': '10'},
    'BRAD': {'n': 'Bradesco', 'r': '20'},
    'SANTANDER': {'n': 'Santander', 'r': '30'},
    'BRASIL': {'n': 'B. Brasil', 'r': '01'},
    'CAIXA': {'n': 'Caixa', 'r': '05'},
}

# --- INTERFACE PRINCIPAL ---
st.title("🏦 Portal de Conciliação Contábil IA")
st.markdown("Sistema automatizado para conferência de impostos e comprovantes bancários.")

with st.sidebar:
    st.header("🤖 Inteligência Artificial")
    ia_on = st.toggle("Ativar IA (Gemini)", value=True)
    if not api_key: st.warning("⚠️ Chave API não configurada nos Secrets.")
    
    st.header("⚙️ Plano de Contas")
    mapa_imp = {}
    with st.expander("Contas de Impostos"):
        for cod, info in DEFAULTS_IMPOSTOS.items():
            c = st.text_input(f"{info['n']} ({cod})", info['c'], key=f"i_{cod}")
            mapa_imp[cod] = {'conta': c, 'nome': info['n']}
    
    mapa_bancos = {}
    with st.expander("Contas de Bancos"):
        for k, v in DEFAULTS_BANCOS.items():
            r = st.text_input(f"Cod. {v['n']}", v['r'], key=f"b_{k}")
            mapa_bancos[k] = {'reduzido': r, 'nome': v['n']}

# ÁREA DE UPLOAD
u1, u2 = st.columns(2)
ex_file = u1.file_uploader("📂 Relatório Domínio (Excel)", type=["xlsx", "xls"])
pdf_files = u2.file_uploader("📄 Comprovantes (PDFs)", type=["pdf"], accept_multiple_files=True)

if ex_file and pdf_files:
    df_dom = None
    for p in range(15):
        try:
            tmp = pd.read_excel(ex_file, skiprows=p)
            cols = [str(c).lower().strip() for c in tmp.columns]
            if any("data" in c or "dt" in c for c in cols) and any("valor" in c or "vlr" in c for c in cols):
                c_d = next(c for c in tmp.columns if "data" in str(c).lower() or "dt" in str(c).lower())
                c_v = next(c for c in tmp.columns if "valor" in str(c).lower() or "vlr" in str(c).lower())
                df_dom = tmp; break
        except: continue

    if df_dom is not None:
        list_pdf = []
        for p in pdf_files:
            info = extrair_detalhes_pdf(p, mapa_bancos, ia_on)
            if info: info['Arq'] = p.name; list_pdf.append(info)
        
        rows = []
        pdf_usados = set()
        
        for _, linha in df_dom.iterrows():
            v_ex = limpar_valor(linha[c_v])
            if v_ex == 0: continue
            d_ex = padronizar_data(linha[c_d])
            match = False
            
            for d in list_pdf:
                if abs(v_ex - d['Total']) < 0.01 and d_ex in d['Data']:
                    i_info = mapa_imp.get(d['Cod'], {'conta': '9999', 'nome': d.get('IA_Imp', 'FORNECEDOR/OUTROS')})
                    b_info = mapa_bancos.get(d['Banc'], {'nome': 'BANCO', 'reduzido': '99'})
                    
                    rows.append({
                        'Status': '✅ CONCILIADO', 
                        'Data': d_ex, 
                        'Valor Total': v_ex,
                        'Imposto': i_info['nome'],
                        'Favorecido': d['Fav'] if d['Fav'] else limpar_nome_contabil(linha.get('Cliente', '')),
                        'Débito': i_info['conta'], 
                        'Crédito': b_info['reduzido'], 
                        'Histórico': f"PAGTO {i_info['nome']} VIA {b_info['nome']} REF {d_ex}",
                        'Principal': d['Principal'], 'Multa': d['Multa'], 'Juros': d['Juros'],
                        'IA': '✨' if d['IA'] else '', 'Arquivo': d['Arq']
                    })
                    pdf_usados.add(d['Arq']); match = True; break
            
            if not match:
                rows.append({
                    'Status': '❌ FALTA PDF', 
                    'Data': d_ex, 
                    'Valor Total': v_ex,
                    'Imposto': '-',
                    'Favorecido': limpar_nome_contabil(linha.get('Cliente', '')),
                    'Débito': '9999', 'Crédito': '99', 
                    'Histórico': 'NÃO LOCALIZADO',
                    'Principal': 0.0, 'Multa': 0.0, 'Juros': 0.0, 
                    'IA': '', 'Arquivo': ''
                })

        for d in list_pdf:
            if d['Arq'] not in pdf_usados:
                i_info = mapa_imp.get(d['Cod'], {'conta': '9999', 'nome': d.get('IA_Imp', 'NÃO IDENTIFICADO')})
                rows.append({
                    'Status': '⚠️ SÓ NO PDF', 
                    'Data': d['Data'][0] if d['Data'] else "-", 
                    'Valor Total': d['Total'],
                    'Imposto': i_info['nome'],
                    'Favorecido': d['Fav'],
                    'Débito': i_info['conta'], 'Crédito': '99', 
                    'Histórico': 'PDF SEM LANÇAMENTO NO EXCEL', 
                    'Principal': d['Principal'], 'Multa': d['Multa'], 'Juros': d['Juros'],
                    'IA': '✨' if d['IA'] else '', 'Arquivo': d['Arq']
                })

        res_df = pd.DataFrame(rows)
        
        # Dashboard
        k1, k2, k3, k4 = st.columns(4)
        conc = len(res_df[res_df['Status'] == '✅ CONCILIADO'])
        falta = len(res_df[res_df['Status'] == '❌ FALTA PDF'])
        sobra = len(res_df[res_df['Status'] == '⚠️ SÓ NO PDF'])
        
        k1.metric("Conciliados", conc)
        k2.metric("Pendentes (Excel)", falta, delta_color="inverse")
        k3.metric("Pendentes (PDF)", sobra)
        k4.metric("Total Processado", formatar_moeda(res_df['Valor Total'].sum()))

        st.divider()
        c_h1, c_h2 = st.columns([2, 1])
        with c_h1: st.subheader("📋 Detalhamento da Conciliação")
        with c_h2: filtro = st.multiselect("Filtrar por Status:", res_df['Status'].unique(), default=res_df['Status'].unique())
        
        df_f = res_df[res_df['Status'].isin(filtro)]

        # Estilização
        def style_rows(row):
            if row['Status'] == '✅ CONCILIADO': return ['background-color: rgba(46, 204, 113, 0.08)'] * len(row)
            if row['Status'] == '❌ FALTA PDF': return ['background-color: rgba(231, 76, 60, 0.08)'] * len(row)
            return ['background-color: rgba(241, 196, 15, 0.08)'] * len(row)

        disp = df_f.copy()
        
        # Formatação de Moedas antes de tratar nulos e vazios
        for col in ['Valor Total', 'Principal', 'Multa', 'Juros']:
            disp[col] = disp[col].apply(formatar_moeda)
        
        # Substituição robusta de nulos e vazios por traço para limpeza visual
        disp = disp.fillna("-")
        for col in disp.columns:
            disp[col] = disp[col].apply(lambda x: "-" if str(x).strip() == "" else x)

        st.dataframe(disp.style.apply(style_rows, axis=1), use_container_width=True)

        # Exportação
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine='xlsxwriter') as wr: res_df.to_excel(wr, index=False)
        st.download_button("📥 Baixar Excel Completo", out.getvalue(), "conciliacao_contabil_final.xlsx")
    else:
        st.error("❌ Erro ao ler Excel. Verifique o cabeçalho.")
else:
    st.info("💡 Arraste o Excel do Domínio e os PDFs dos comprovantes para começar.")
