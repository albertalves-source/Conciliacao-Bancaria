import streamlit as st
import pandas as pd
import pdfplumber
import re
import io
import warnings
import requests
import json
import time

# Configurações de Página - Nome e Ícone do site
st.set_page_config(page_title="Portal de Conciliação Contábil IA", layout="wide", page_icon="🤖")
warnings.filterwarnings("ignore")

# --- CONFIGURAÇÃO DA IA (GEMINI) ---
api_key = "" # A chave é injetada automaticamente pelo ambiente

def consultar_ia(texto_pdf):
    """Consulta a IA Gemini para identificar impostos ou bancos desconhecidos."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={api_key}"
    
    prompt = f"""
    Você é um assistente contábil especialista em impostos brasileiros e sistemas bancários.
    Analise o seguinte texto extraído de um comprovante PDF e identifique:
    1. Nome do Imposto (ex: IRPJ, ISS, Taxa de Licenciamento).
    2. Código da Receita (4 dígitos, se houver).
    3. Nome do Banco.
    
    Responda APENAS em formato JSON como no exemplo:
    {{"imposto_nome": "Nome Encontrado", "codigo_receita": "0000", "banco_nome": "Nome do Banco"}}
    
    Texto do PDF:
    {texto_pdf[:2000]}
    """
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"responseMimeType": "application/json"}
    }
    
    for delay in [1, 2, 4]:
        try:
            response = requests.post(url, json=payload, timeout=30)
            if response.status_code == 200:
                result = response.json()
                content = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '{}')
                return json.loads(content)
            time.sleep(delay)
        except:
            time.sleep(delay)
    return None

# --- ESTILIZAÇÃO PARA ADAPTAÇÃO DE TEMA ---
st.markdown("""
    <style>
    /* Estilo para os cartões de métricas se adaptarem ao tema */
    div[data-testid="metric-container"] {
        border: 1px solid rgba(128, 128, 128, 0.2);
        padding: 20px;
        border-radius: 15px;
        background-color: rgba(128, 128, 128, 0.05);
    }
    .stDataFrame { border-radius: 12px; }
    </style>
    """, unsafe_allow_html=True)

# --- FUNÇÕES DE APOIO ---
def formatar_moeda(v):
    """Formata valor para padrão R$ 1.234,56."""
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def limpar_valor(v):
    if pd.isna(v): return 0.0
    if isinstance(v, (int, float)): return float(v)
    v_limpo = str(v).replace('R$', '').replace('$', '').strip()
    if ',' in v_limpo and '.' in v_limpo: v_limpo = v_limpo.replace('.', '').replace(',', '.')
    elif ',' in v_limpo: v_limpo = v_limpo.replace(',', '.')
    try: return float(v_limpo)
    except: return 0.0

def padronizar_data(data_obj):
    try: return pd.to_datetime(data_obj, dayfirst=True).strftime('%d/%m/%Y')
    except:
        match = re.search(r'(\d{2}/\d{2}/\d{4})', str(data_obj))
        return match.group(1) if match else str(data_obj)

def limpar_nome_favorecido(nome):
    if not nome or nome == "N/A": return "N/A"
    nome = re.sub(r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{3}\.\d{3}\.\d{3}-\d{2}', '', nome)
    termos = ["DATA DO", "PAGAMENTO", "BENEFICIARIO", "RAZAO SOCIAL", "NOME", "FAVORECIDO", "VALOR PAGO"]
    for t in termos: nome = re.sub(t, '', nome, flags=re.IGNORECASE)
    return nome.replace(':', '').strip().upper()

def extrair_detalhes_pdf(file_pdf, mapa_bancos, usar_ia=False):
    detalhes = {'Data': [], 'Total': 0.0, 'Principal': 0.0, 'Multa': 0.0, 'Juros': 0.0, 
                'Codigo_Receita': "N/A", 'Favorecido': "N/A", 'Banco': "N/A", 'IA_Usada': False}
    try:
        with pdfplumber.open(file_pdf) as pdf:
            texto = pdf.pages[0].extract_text()
            if not texto: return None
            texto_upper = texto.upper()
            detalhes['Data'] = list(set(re.findall(r'(\d{2}/\d{2}/\d{4})', texto)))
            receita = re.search(r'(?:RECEITA|CODIGO|RECEITA:)\s*(\d{4})', texto, re.IGNORECASE)
            if receita: detalhes['Codigo_Receita'] = receita.group(1)
            
            for termo_busca in mapa_bancos.keys():
                if termo_busca in texto_upper:
                    detalhes['Banco'] = termo_busca
                    break
            
            valores_txt = re.findall(r'(\d[\d\.]*,\d{2})', texto)
            if valores_txt:
                detalhes['Total'] = limpar_valor(valores_txt[-1])
                if len(valores_txt) >= 4:
                    detalhes['Principal'] = limpar_valor(valores_txt[-4])
                    detalhes['Multa'] = limpar_valor(valores_txt[-3])
                    detalhes['Juros'] = limpar_valor(valores_txt[-2])
                else: detalhes['Principal'] = detalhes['Total']
            
            if usar_ia and (detalhes['Codigo_Receita'] == "N/A" or detalhes['Banco'] == "N/A"):
                with st.spinner(f"IA analisando {file_pdf.name}..."):
                    ia_res = consultar_ia(texto)
                    if ia_res:
                        if detalhes['Codigo_Receita'] == "N/A": 
                            detalhes['Codigo_Receita'] = ia_res.get('codigo_receita', "N/A")
                            detalhes['IA_Imposto'] = ia_res.get('imposto_nome')
                        if detalhes['Banco'] == "N/A": 
                            detalhes['IA_Banco'] = ia_res.get('banco_nome')
                        detalhes['IA_Usada'] = True

            for linha in texto.split('\n'):
                if any(x in linha.upper() for x in ["BENEFICIARIO", "RAZAO SOCIAL", "NOME", "FAVORECIDO"]):
                    bruto = linha.split(':')[-1].strip() if ':' in linha else linha
                    detalhes['Favorecido'] = limpar_nome_favorecido(bruto)
                    break
    except: pass
    return detalhes

# --- BIBLIOTECA PADRÃO DE IMPOSTOS ---
DEFAULTS_IMPOSTOS = {
    '0561': {'nome': 'IRRF s/ Salários', 'conta': '2105'},
    '2172': {'nome': 'COFINS - Faturamento', 'conta': '2108'},
    '8109': {'nome': 'PIS - Faturamento', 'conta': '2110'},
    '5952': {'nome': 'CSRF (PIS/COF/CSLL)', 'conta': '2115'},
}

DEFAULTS_BANCOS = {
    'ITAU': {'nome': 'Itaú', 'reduzido': '10'},
    'BRAD': {'nome': 'Bradesco', 'reduzido': '20'},
    'SANTANDER': {'nome': 'Santander', 'reduzido': '30'},
    'BRASIL': {'nome': 'Banco do Brasil', 'reduzido': '01'},
    'CAIXA': {'nome': 'Caixa Econômica', 'reduzido': '05'},
}

# --- INTERFACE PRINCIPAL ---
st.title("🏦 Portal de Conciliação com IA")
st.markdown("Automatize a conferência contábil e gere lançamentos para o sistema Domínio.")

with st.sidebar:
    st.header("🤖 Inteligência Artificial")
    usar_ia = st.toggle("Ativar IA (Gemini 2.5 Flash)", value=True)
    
    st.header("⚙️ Plano de Contas")
    mapa_imp = {}
    with st.expander("Contas de Impostos"):
        for cod, info in DEFAULTS_IMPOSTOS.items():
            conta = st.text_input(f"{info['nome']} ({cod})", info['conta'], key=f"imp_{cod}")
            mapa_imp[cod] = {'conta': conta, 'nome': info['nome']}
    
    mapa_banc = {}
    with st.expander("Contas de Bancos"):
        for k, v in DEFAULTS_BANCOS.items():
            reduzido = st.text_input(f"Reduzido {v['nome']}", v['reduzido'], key=f"banc_{k}")
            mapa_banc[k] = {'reduzido': reduzido, 'nome': v['nome']}

# ÁREA DE UPLOAD
col_up1, col_up2 = st.columns(2)
with col_up1:
    excel_file = st.file_uploader("📂 Relatório Domínio (Excel)", type=["xlsx", "xls"])
with col_up2:
    pdf_files = st.file_uploader("📄 Comprovantes (PDFs)", type=["pdf"], accept_multiple_files=True)

if excel_file and pdf_files:
    # 1. Carregar Excel
    df_dom = None
    for p in range(15):
        try:
            temp = pd.read_excel(excel_file, skiprows=p)
            cols = [str(c).lower().strip() for c in temp.columns]
            if any("data" in c or "dt" in c for c in cols) and any("valor" in c or "vlr" in c for c in cols):
                c_data = next(c for c in cols if "data" in c or "dt" in c)
                c_valor = next(c for c in cols if "valor" in c or "vlr" in c)
                mapeamento = {str(orig).lower().strip(): orig for orig in temp.columns}
                col_data_real, col_valor_real = mapeamento[c_data], mapeamento[c_valor]
                df_dom = temp
                break
        except: continue

    if df_dom is not None:
        # 2. Processar PDFs
        list_pdf_data = []
        for pdf in pdf_files:
            info = extrair_detalhes_pdf(pdf, mapa_banc, usar_ia=usar_ia)
            if info: info['Arquivo'] = pdf.name; list_pdf_data.append(info)
        
        # 3. Cruzamento de Dados
        final_rows = []
        pdfs_usados = set()
        for _, linha in df_dom.iterrows():
            v_dom = limpar_valor(linha[col_valor_real])
            if v_dom == 0: continue
            d_dom = padronizar_data(linha[col_data_real])
            match = False
            for doc in list_pdf_data:
                if abs(v_dom - doc['Total']) < 0.01 and d_dom in doc['Data']:
                    imp_info = mapa_imp.get(doc['Codigo_Receita'], {'conta': '9999', 'nome': doc.get('IA_Imposto', 'FORNECEDOR/OUTROS')})
                    banc_info = mapa_banc.get(doc['Banco'], {'nome': doc.get('IA_Banco', 'BANCO'), 'reduzido': '99'})
                    
                    final_rows.append({
                        'Status': '✅ CONCILIADO', 'Data': d_dom, 'Valor Total': v_dom,
                        'Débito': imp_info['conta'], 'Crédito': banc_info['reduzido'],
                        'Imposto': imp_info['nome'], 'Histórico': f"PAGTO {imp_info['nome']} VIA {banc_info['nome']} REF {d_dom}",
                        'Principal': doc['Principal'], 'Multa': doc['Multa'], 'Juros': doc['Juros'],
                        'IA': '✨' if doc['IA_Usada'] else '', 'Arquivo': doc['Arquivo'], 'Favorecido': doc['Favorecido']
                    })
                    pdfs_usados.add(doc['Arquivo']); match = True; break
            if not match:
                final_rows.append({
                    'Status': '❌ FALTA PDF', 'Data': d_dom, 'Valor Total': v_dom,
                    'Débito': '9999', 'Crédito': '99', 'Imposto': 'N/A', 'IA': '', 'Arquivo': 'N/A',
                    'Favorecido': limpar_nome_favorecido(str(linha.get('Cliente', 'N/A')))
                })

        # Sobras de PDFs
        for doc in list_pdf_data:
            if doc['Arquivo'] not in pdfs_usados:
                imp_info = mapa_imp.get(doc['Codigo_Receita'], {'conta': '9999', 'nome': doc.get('IA_Imposto', 'PDF NÃO IDENTIFICADO')})
                final_rows.append({
                    'Status': '⚠️ SÓ NO PDF', 'Data': doc['Data'][0] if doc['Data'] else "N/A", 'Valor Total': doc['Total'],
                    'Débito': imp_info['conta'], 'Crédito': '99', 'Imposto': imp_info['nome'],
                    'Principal': doc['Principal'], 'Multa': doc['Multa'], 'Juros': doc['Juros'],
                    'IA': '✨' if doc['IA_Usada'] else '', 'Arquivo': doc['Arquivo'], 'Favorecido': doc['Favorecido']
                })

        res_df = pd.DataFrame(final_rows).fillna(0)
        
        # DASHBOARD DE INDICADORES
        k1, k2, k3, k4 = st.columns(4)
        conc = len(res_df[res_df['Status'] == '✅ CONCILIADO'])
        falta = len(res_df[res_df['Status'] == '❌ FALTA PDF'])
        sobra = len(res_df[res_df['Status'] == '⚠️ SÓ NO PDF'])
        total_v = res_df['Valor Total'].sum()

        k1.metric("✅ Conciliados", conc)
        k2.metric("❌ Pendentes (Excel)", falta, delta_color="inverse")
        k3.metric("⚠️ Pendentes (PDF)", sobra)
        k4.metric("💰 Total Processado", formatar_moeda(total_v))

        # ÁREA DE RESULTADOS COM FILTRO
        st.divider()
        col_header1, col_header2 = st.columns([2, 1])
        with col_header1:
            st.subheader("📋 Detalhamento da Conciliação")
        with col_header2:
            filtro = st.multiselect("Filtrar por Status:", res_df['Status'].unique(), default=res_df['Status'].unique())
        
        # Aplicar Filtro
        df_filtrado = res_df[res_df['Status'].isin(filtro)]

        # Estilização Horizontal
        def style_rows(row):
            if row['Status'] == '✅ CONCILIADO': return ['background-color: rgba(46, 204, 113, 0.1)'] * len(row)
            if row['Status'] == '❌ FALTA PDF': return ['background-color: rgba(231, 76, 60, 0.1)'] * len(row)
            return ['background-color: rgba(241, 196, 15, 0.1)'] * len(row)

        # Formatação de exibição
        df_display = df_filtrado.copy()
        for col in ['Valor Total', 'Principal', 'Multa', 'Juros']:
            df_display[col] = df_display[col].apply(formatar_moeda)

        # Tabela Principal
        st.dataframe(df_display.style.apply(style_rows, axis=1), use_container_width=True)

        # Exportação
        st.divider()
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            res_df.to_excel(writer, index=False)
        
        st.download_button(
            label="📥 Exportar Lançamentos Finais (.xlsx)", 
            data=output.getvalue(), 
            file_name="conciliacao_finalizada.xlsx",
            help="Clique para baixar o relatório completo pronto para o sistema Domínio."
        )
    else:
        st.error("❌ Erro: Não foi possível identificar os dados no Excel. Verifique se as colunas de Data e Valor existem.")
else:
    st.info("💡 Bem-vindo! Comece enviando o relatório do Domínio e os PDFs dos comprovantes para análise.")