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

def limpar_nome_contabil(nome):
    """Limpeza Suprema: Destrói qualquer ID técnico e limpa fantasmas."""
    if not nome or str(nome).lower() in ["n/a", "nan", "0", "none"]: return ""
    
    n = str(nome).upper()
    
    # 1. Remove REGEX pesados (CPFs, UUIDs, Alfanuméricos do PIX)
    n = re.sub(r'[A-Z0-9]{8}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{12}', '', n)
    n = re.sub(r'\b[A-Z0-9]*\d[A-Z0-9]*\b', '', n) # Destrói qualquer palavra que tenha número no meio
    
    # 2. Remove "R$" e variações que causam linhas fantasmas
    n = n.replace('R$', '').replace('$', '').replace('DE R', '')
    
    # 3. Termos técnicos de extrato
    termos_bancarios = [
        "PIX ENVIADO PARA:", "PIX RECEBIDO PAGADOR:", "PIX ENVIADO PARA", "PIX RECEBIDO",
        "TRANSFERENCIA ENVIADA PARA:", "TRANSFERÊNCIA ENVIADA PARA:", "TRANSFERÊNCIA ENVIADA PARA", 
        "TRANSFERENCIA RECEBIDA PAGADOR:", "TRANSFERÊNCIA RECEBIDA PAGADOR:", "TRANSFERÊNCIA RECEBIDA",
        "TED ENVIADA PARA:", "TED ENVIADA PARA", "TED RECEBIDA", 
        "DEVOLUÇÃO DE PIX ENVIADO (DESFAZIMENTO)", "DEVOLUCAO DE PIX ENVIADO (DESFAZIMENTO)",
        "DEVOLUÇÃO DE PIX ENVIADO", "DESFAZIMENTO", 
        "PAGADOR:", "BENEFICIARIO:", "RAZAO SOCIAL:", "FAVORECIDO:", "VALOR PAGO", "DATA DO PAGAMENTO", 
        "PAGAMENTO DE BOLETO", "BOLETO", "PAYMENT", "SALDO DISPONÍVEL", "SALDO DISPONIVEL", "COMPROVANTE FISCAL", "COMPROVANTE", "SALDO"
    ]
    for t in termos_bancarios:
        n = n.replace(t, '')
        
    # 4. Limpa pontuação residual
    n = re.sub(r'[\.\-\/\,:\(\)_]', ' ', n)
    
    # 5. Remove palavras vazias ou sobras minúsculas
    palavras = [w for w in n.split() if len(w) > 1]
    resultado = ' '.join(palavras).strip()
    
    if not resultado or resultado in ["DE", "DA", "DO", "PARA", "EM"]: return ""
    return resultado

def extrair_dados_arquivo(file, mapa_bancos, mapa_imp, usar_ia, termos_ignorar, modo_conciliacao):
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
                        
                        linha_upper = linha.upper()
                        
                        # Filtro Anti-Lixo Mestre
                        if any(x in linha_upper for x in ["SALDO", "RESUMO", "DISPONÍVEL", "DISPONIVEL", "VALOR TOTAL", "TOTAL ACUMULADOR", "SALDO EM"]): continue
                        
                        # FILTRO DE NATUREZA: Diferencia Entradas (Verde) de Saídas (Vermelho)
                        is_credito = any(x in linha_upper for x in ["RECEBID", "DEVOLU", "DESFAZIMENTO", "ESTORNO", "RESSARCIMENTO"])
                        
                        if "Pagar" in modo_conciliacao and is_credito: continue 
                        if "Receber" in modo_conciliacao and not is_credito: continue 
                        
                        # Filtro Personalizado do Utilizador
                        if any(t in linha_upper for t in termos_ignorar if t): continue
                        
                        data_match = re.search(r'(\d{2}/\d{2}/\d{4})', linha)
                        valor_match = re.findall(r'(\d[\d\.]*,\d{2})', linha)
                        
                        if data_match and valor_match:
                            desc_bruta = linha.replace(data_match.group(1), "")
                            for v_txt in valor_match: desc_bruta = desc_bruta.replace(v_txt, "")
                            
                            nome_limpo = limpar_nome_contabil(desc_bruta)
                            if not nome_limpo: continue

                            cod_found = ""
                            codes = re.findall(r'\b(\d{4})\b', linha)
                            for c in codes:
                                if c in mapa_imp: cod_found = c; break
                            
                            for v_txt in valor_match:
                                val = limpar_valor(v_txt)
                                if val > 0:
                                    transacoes.append({
                                        'Data': [data_match.group(1)], 'Total': val,
                                        'Cod': cod_found, 'Fav': nome_limpo, 
                                        'Banc': banco_base, 'IA': False, 'Arq': file.name,
                                        'Principal': val, 'Multa': 0.0, 'Juros': 0.0
                                    })
                
                if not transacoes:
                    texto_completo = "\n".join([p.extract_text() or "" for p in pdf.pages])
                    texto_upper = texto_completo.upper()
                    
                    is_credito_doc = any(x in texto_upper for x in ["RECEBID", "DEVOLU", "DESFAZIMENTO", "ESTORNO"])
                    if "Pagar" in modo_conciliacao and is_credito_doc: return []
                    if "Receber" in modo_conciliacao and not is_credito_doc: return []
                    
                    rec = re.search(r'(?:RECEITA|CODIGO|RECEITA:)\s*(\d{4})', texto_completo, re.IGNORECASE)
                    datas = list(set(re.findall(r'(\d{2}/\d{2}/\d{4})', texto_completo)))
                    valores = re.findall(r'(\d[\d\.]*,\d{2})', texto_completo)
                    if datas and valores:
                        v_f = limpar_valor(valores[-1])
                        transacoes.append({
                            'Data': datas, 'Total': v_f, 'Cod': rec.group(1) if rec else "",
                            'Banc': banco_base, 'Fav': "COMPROVANTE FISCAL",
                            'IA': False, 'Arq': file.name, 'Principal': v_f, 'Multa': 0.0, 'Juros': 0.0
                        })
        except: pass
        
    return transacoes

# ==========================================
# 🧠 BANCO DE DADOS MULTI-EMPRESAS
# Albert, pode adicionar quantas empresas quiser aqui abaixo!
# ==========================================
BANCO_DE_DADOS_EMPRESAS = {
    "SELECT OPERATIONS S.A.": {
        "impostos": {
            '0561': {'n': 'IRRF s/ Salários', 'c': '2105'}, 
            '2172': {'n': 'COFINS Faturamento', 'c': '2108'}, 
            '8109': {'n': 'PIS Faturamento', 'c': '2110'}
        },
        "bancos": {
            'ITAU': {'n': 'Itaú', 'r': '10'}, 
            'BRAD': {'n': 'Bradesco', 'r': '20'}, 
            'SANTANDER': {'n': 'Santander', 'r': '30'}, 
            'BRASIL': {'n': 'Banco do Brasil', 'r': '8'}, 
            'DELFIN': {'n': 'Delfinance MMABET', 'r': '1107'}, 
            'DELFINANCE': {'n': 'Delfinance MMABET', 'r': '1107'}
        },
        "fornecedores": {
            'RT BRASIL CONSULTORIA E EMPREENDIMENTOS FINANCEIROS LTDA': '2050',
            'INTERNATIONAL BET ASSESSORIA E CONSULTORIA EM MARKETING DIGITAL LTDA': '2051',
            'BUZZCRAFT DIGITAL LTDA': '2052',
            'AM PUBLICIDADE E PROMOCAO DE VENDAS LTDA': '2053',
            'UNIFICAPAY SERVICOS FINANCEIROS E DE PAGAMENTOS LTDA': '2054',
            'PAGLIVRE SOLUCOES EM COBRANCA LTDA': '2055',
            'DUCAMPELO PARTICIPACOES LTDA': '2056'
            # Adicione mais fornecedores da Select Operations aqui...
        }
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
            'BRASIL': {'n': 'B. Brasil', 'r': '99'}, 
            'DELFIN': {'n': 'Delfinance', 'r': '99'}
        },
        "fornecedores": {} # Vazio, usará sempre 9999
    }
    # Para adicionar a "EMPRESA B", basta copiar o bloco acima e colar aqui com os novos códigos!
}

# --- INTERFACE ---
st.title("🏦 Conciliador Contábil IA V24.0")
st.markdown("Plataforma Multi-Empresas: Códigos contábeis adaptam-se automaticamente à empresa selecionada.")

with st.sidebar:
    st.header("🏢 Empresa em Conciliação")
    empresa_selecionada = st.selectbox(
        "Qual empresa está a conciliar agora?", 
        list(BANCO_DE_DADOS_EMPRESAS.keys())
    )
    
    # Carrega as configurações da empresa escolhida
    config_atual = BANCO_DE_DADOS_EMPRESAS[empresa_selecionada]
    mapa_imp = config_atual["impostos"]
    mapa_bancos = config_atual["bancos"]
    mapa_fornecedores = config_atual["fornecedores"]
    
    st.divider()
    st.header("🎯 Natureza da Conciliação")
    modo_conciliacao = st.radio(
        "O que estamos a conciliar?", 
        ["Contas a Pagar (Apenas Débitos/Vermelho)", 
         "Contas a Receber (Apenas Créditos/Verde)", 
         "Ambos (Extrato Completo)"],
        index=0
    )
    
    st.divider()
    st.header("⚙️ Parâmetros")
    tolerancia_dias = st.slider("Tolerância de Datas (dias):", 0, 10, 3)
    
    st.divider()
    st.header("🚫 Filtro de Extrato")
    st.markdown("<small>Ignorar linhas que contenham as palavras:</small>", unsafe_allow_html=True)
    ignorar_txt = st.text_area("", "CONNECTPSP, SALDO, RESUMO")
    termos_ignorar = [t.strip().upper() for t in ignorar_txt.split(',')]
    
    st.divider()
    st.success(f"✅ Códigos carregados para: **{empresa_selecionada}**")

c1, c2 = st.columns(2)
with c1: excel_file = st.file_uploader("📂 Relatório Domínio (Excel/CSV)", type=["xlsx", "xls", "csv"])
with c2: receipt_files = st.file_uploader("📄 PDFs/Extratos (Múltiplos)", type=["pdf", "png", "jpg"], accept_multiple_files=True)

if excel_file and receipt_files:
    try:
        if excel_file.name.endswith('.csv'):
            df_dom = pd.read_csv(excel_file, engine='python')
            df_dom = df_dom.dropna(how='all', axis=1)
        else:
            df_dom = pd.read_excel(excel_file)
            
        df_dom.columns = [str(c).replace('\n', ' ').strip() for c in df_dom.columns]
        df_dom = df_dom[~df_dom.astype(str).apply(lambda x: x.str.contains('Total Acumulador', case=False, na=False)).any(axis=1)]
        
        c_d = next((c for c in df_dom.columns if "data" in c.lower()), None)
        c_v = next((c for c in df_dom.columns if "valor" in c.lower() and "cont" in c.lower()), next((c for c in df_dom.columns if "valor" in c.lower() or "vlr" in c.lower()), None))
        c_cli = next((c for c in df_dom.columns if any(x in c.lower() for x in ["fornecedor", "cliente", "nome"])), "Fornecedor")
        
        df_dom = df_dom.reset_index(drop=True)
    except Exception as e:
        st.error(f"Erro ao ler ficheiro: {e}"); st.stop()

    todas_transacoes_pdf = []
    for f in receipt_files:
        with st.spinner(f"A processar {f.name}..."):
            todas_transacoes_pdf.extend(extrair_dados_arquivo(f, mapa_bancos, mapa_imp, True, termos_ignorar, modo_conciliacao))

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
                        b_inf = next((v for k, v in mapa_bancos.items() if k in str(doc['Banc']).upper()), {'nome': doc.get('Banc', 'BANCO'), 'reduzido': '9999'})
                        
                        fav_final = str(l.get(c_cli, '')).upper()
                        if fav_final == "NAN" or not fav_final: fav_final = doc['Fav']
                        
                        # Cérebro Multi-Empresas: Determinar Conta de Débito
                        conta_debito = '9999'
                        if regra_imp['nome'] != '-':
                            conta_debito = regra_imp['conta']
                        else:
                            # Procura a correspondência no mapa da empresa selecionada
                            if fav_final in mapa_fornecedores:
                                conta_debito = mapa_fornecedores[fav_final]
                            else:
                                for f_nome, f_conta in mapa_fornecedores.items():
                                    if f_nome in fav_final:
                                        conta_debito = f_conta
                                        break

                        rows.append({
                            'Status': '✅ CONCILIADO', 'Data Excel': d_ex_obj.strftime('%d/%m/%Y'), 'Valor Total': v_ex,
                            'Imposto': regra_imp['nome'], 'Favorecido': fav_final, 'Data PDF': d_pdf_obj.strftime('%d/%m/%Y'),
                            'Banco': b_inf['nome'], 'Débito': conta_debito, 'Crédito': b_inf['reduzido'], 
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
            b_inf = next((v for k, v in mapa_bancos.items() if k in str(doc['Banc']).upper()), {'nome': doc.get('Banc', 'BANCO'), 'reduzido': '9999'})
            rows.append({'Status': '⚠️ SÓ NO PDF', 'Data PDF': doc['Data'][0], 'Valor Total': doc['Total'], 'Imposto': mapa_imp.get(doc['Cod'], {'nome':'-'})['nome'], 'Favorecido': doc['Fav'], 'Banco': b_inf['nome'], 'Arquivo': doc['Arq']})

    res_df = pd.DataFrame(rows).fillna("-")
    st.subheader(f"📋 Relatório de Conciliação - {empresa_selecionada}")
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
    
    nome_arquivo = f"conciliacao_{empresa_selecionada.split()[0].lower()}.xlsx"
    st.download_button("📥 Baixar Excel Multi-Empresas", out.getvalue(), nome_arquivo)
