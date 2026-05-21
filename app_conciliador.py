import streamlit as st
import pandas as pd
import re
import io
import warnings

# Tenta importar bibliotecas extras de forma segura
try:
    import pdfplumber
except ImportError:
    st.error("Erro: A biblioteca 'pdfplumber' não foi encontrada. Instale-a para processar os extratos em PDF.")

# Configurações de Página
st.set_page_config(page_title="Portal de Conciliação - Inteligência Contábil", layout="wide", page_icon="🏦")
warnings.filterwarnings("ignore")

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
    if '.' in v_str and ',' in v_str:
        v_str = v_str.replace('.', '').replace(',', '.')
    elif ',' in v_str:
        v_str = v_str.replace(',', '.')
    try: return float(v_str)
    except: return 0.0

def converter_data_dominio(data_obj):
    if pd.isna(data_obj): return None
    try:
        num = float(data_obj)
        if num > 10000:
            return pd.to_datetime(num, unit='D', origin='1899-12-30').date()
    except: pass
    try: 
        return pd.to_datetime(data_obj, dayfirst=True).date()
    except:
        match = re.search(r'(\d{2}/\d{2}/\d{4})', str(data_obj))
        if match: return datetime.strptime(match.group(1), '%d/%m/%Y').date()
        match_iso = re.search(r'(\d{4}-\d{2}-\d{2})', str(data_obj))
        if match_iso: return datetime.strptime(match_iso.group(1), '%Y-%m-%d').date()
        return None

def normalizar_espacos(texto):
    if not isinstance(texto, str): return ""
    return " ".join(texto.upper().split())

def extrair_dados_arquivo(file, termos_ignorar):
    transacoes = []
    if file.name.lower().endswith(".pdf"):
        try:
            with pdfplumber.open(file) as pdf:
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
                        if any(x in linha_upper for x in ["SALDO INICIAL", "SALDO FINAL", "RESUMO", "TOTAL ACUMULADOR"]): continue
                        if any(t in linha_upper for t in termos_ignorar if t): continue
                        
                        is_credito = False
                        if any(x in linha_upper for x in ["RECEBID", "DEVOLU", "ESTORNO", "CREDITO", "CRÉDITO", "DEPÓSITO", "TED RECEBIDA"]):
                            is_credito = True
                        
                        data_match = re.search(r'(\d{2}/\d{2}/\d{4})', linha)
                        # REGEX BLINDADO: Captura o valor cheio sem cortar os milhares por causa do ponto brasileiro
                        valor_match = re.findall(r'-?[\d.]*,\d{2}', linha)
                        
                        if data_match and valor_match:
                            desc_bruta = linha.replace(data_match.group(1), "")
                            for v_txt in valor_match: desc_bruta = desc_bruta.replace(v_txt, "")
                            
                            nome_limpo = re.sub(r'[A-Z0-9]{8}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{12}', '', desc_bruta.upper())
                            nome_limpo = re.sub(r'\b[A-Z0-9]*\d[A-Z0-9]*\b', '', nome_limpo)
                            for t in ["PAGAMENTO VIA PIX", "PAGAMENTO DE BOLETO", "TRANSFERENCIA INTERNA", "R$", "RS", '",', '"']:
                                nome_limpo = nome_limpo.replace(t, '')
                            nome_limpo = normalizar_espacos(nome_limpo).strip('," ')
                            
                            if not nome_limpo or nome_limpo in ["-", ""]:
                                nome_limpo = "PAGAMENTO DE BOLETO / TARIFA"

                            val = abs(limpar_valor(valor_match[0]))
                            if val > 0:
                                transacoes.append({
                                    'Data': data_match.group(1), 
                                    'Total': val,
                                    'Fav': nome_limpo, 
                                    'Is_Credito': is_credito
                                })
        except Exception as e: 
            st.error(f"Erro ao ler PDF: {e}")
            
    return transacoes

# --- INTERFACE STREAMLIT ---
st.title("🏦 Conciliador Bancário Inteligente")
st.markdown("Cruzamento automatizado entre Relatório de Entradas (Fiscal) e Extrato Bancário.")

with st.sidebar:
    st.header("⚙️ Parâmetros de Ajuste")
    ignorar_data = st.checkbox("Ignorar Validação de Datas", value=True)
    tolerancia_dias = 99999 if ignorar_data else st.slider("Tolerância de Dias:", 0, 30, 7)
    
    st.divider()
    ignorar_txt = st.text_area("Ignorar no Extrato (Filtro):", "SALDO INICIAL, SALDO FINAL, TRANSFERENCIA INTERNA ENTRE CONTAS")
    termos_ignorar = [t.strip().upper() for t in ignorar_txt.split(',')]

# --- UPLOAD DOS ARQUIVOS ---
c1, c2 = st.columns(2)
with c1: excel_file = st.file_uploader("📂 Planilha de Entradas (Relatório Fiscal)", type=["xlsx", "xls", "csv"])
with c2: receipt_files = st.file_uploader("📄 Extrato do Banco (PDF)", type=["pdf"], accept_multiple_files=True)

if excel_file and receipt_files:
    try:
        if excel_file.name.endswith('.csv'):
            df_dom = pd.read_csv(excel_file, sep=',', encoding='utf-8-sig')
            if len(df_dom.columns) < 5:
                excel_file.seek(0)
                df_dom = pd.read_csv(excel_file, sep=';')
        else:
            df_dom = pd.read_excel(excel_file)
    except Exception as e:
        st.error(f"Erro ao ler arquivo fiscal: {e}")
        st.stop()

    # Extração dos dados do extrato
    extrato_bancario = []
    for f in receipt_files:
        extrato_bancario.extend(extrair_dados_arquivo(f, termos_ignorar))

    # --- MAPEAMENTO INTELIGENTE DE COLUNAS ---
    df_dom.columns = [str(c).strip() for c in df_dom.columns]
    
    col_data = next((c for c in df_dom.columns if re.search(r'data', c, re.IGNORECASE)), None)
    col_valor = next((c for c in df_dom.columns if re.search(r'valor contábil|valor_total|valor', c, re.IGNORECASE)), None)
    col_forn = next((c for c in df_dom.columns if re.search(r'fornecedor|nome', c, re.IGNORECASE)), None)
    col_nota = next((c for c in df_dom.columns if re.search(r'nota|documento', c, re.IGNORECASE)), None)
    
    # Captura precisa do CÓDIGO DO FORNECEDOR (Ignorando o primeiro código do lançamento)
    colunas_lista = list(df_dom.columns)
    col_codigo_forn = None
    if col_forn:
        idx_forn = colunas_lista.index(col_forn)
        for j in range(idx_forn - 1, -1, -1):
            if any(p in colunas_lista[j].upper() for p in ['CÓDIGO', 'CODIGO', 'COD']):
                col_codigo_forn = colunas_lista[j]
                break

    matriz_final = []
    ids_extrato_usados = set()

    # --- PROCESSO DE CRUZA (PASSO 1: FISCAL) ---
    for idx, row in df_dom.iterrows():
        forn_fiscal = str(row.get(col_forn, ''))
        if pd.isna(row.get(col_forn)) or any(x in forn_fiscal.upper() for x in ["TOTAL", "ACOMPANHAMENTO", "CÓDIGO", "NAN", "NONE"]):
            continue
            
        forn_fiscal_clean = normalizar_espacos(forn_fiscal)
        val_fiscal_bruto = abs(limpar_valor(row.get(col_valor, 0)))
        data_fiscal_obj = converter_data_dominio(row.get(col_data))
        nota_fiscal = str(row.get(col_nota, "-")).split('.')[0]
        
        # Puxa o código do fornecedor localizado pela proximidade do cabeçalho
        cod_forn_real = str(row.get(col_codigo_forn, "-")).split('.')[0] if col_codigo_forn else "-"

        # Tratamento de Impostos Retidos na linha (Caso haja)
        val_irrf = abs(limpar_valor(row.get('Valor', 0))) if 'Valor' in df_dom.columns else 0.0
        val_liquido_esperado = val_fiscal_bruto - val_irrf

        match_banco = None
        for i, trans in enumerate(extrato_bancario):
            if i in ids_extrato_usados: continue
            
            nome_banco_clean = normalizar_espacos(trans['Fav'])
            # Validação de nome por similaridade ou colagem mútua
            nome_bate = (forn_fiscal_clean[:12] in nome_banco_clean) or (nome_banco_clean[:12] in forn_fiscal_clean)
            
            try:
                data_banco_obj = datetime.strptime(trans['Data'], '%d/%m/%Y').date()
                dias_dif = abs((data_fiscal_obj - data_banco_obj).days) if data_fiscal_obj else 999
            except: dias_dif = 999
            
            data_valida = dias_dif <= tolerancia_dias

            if data_valida:
                # Regra 1: Valor zerado na nota mas tem movimento (Ex: KR3W)
                if val_fiscal_bruto == 0.0 and nome_bate and not trans['Is_Credito']:
                    match_banco = trans
                    ids_extrato_usados.add(i)
                    break
                # Regra 2: Valor bate exato (Bruto)
                elif abs(val_fiscal_bruto - trans['Total']) < 0.1 and (nome_bate or val_fiscal_bruto > 5000):
                    match_banco = trans
                    ids_extrato_usados.add(i)
                    break
                # Regra 3: Valor líquido bate por causa de impostos deduzidos
                elif abs(val_liquido_esperado - trans['Total']) < 0.1 and val_irrf > 0:
                    match_banco = trans
                    ids_extrato_usados.add(i)
                    break

        if match_banco:
            matriz_final.append({
                'Data de Ref.': data_fiscal_obj.strftime('%d/%m/%Y') if data_fiscal_obj else match_banco['Data'],
                'Tipo de Lançamento': 'Misto (NF + Pagamento)' if not match_banco['Is_Credito'] else 'Misto (NF + Recebimento)',
                'Nº Nota / Doc': nota_fiscal,
                'Cód. Forn.': cod_forn_real,
                'Participante / Favorecido': forn_fiscal,
                'Valor Nota (R$)': val_fiscal_bruto,
                'Valor Saída (R$)': match_banco['Total'] if not match_banco['Is_Credito'] else 0.0,
                'Valor Entrada (R$)': match_banco['Total'] if match_banco['Is_Credito'] else 0.0,
                'Status / Classificação Contábil': '✅ CONCILIADO' if val_fiscal_bruto > 0 else '⚠️ CONCILIADO COM DIVERGÊNCIA VALOR FISCAL (R$ 0,00)'
            })
        else:
            matriz_final.append({
                'Data de Ref.': data_fiscal_obj.strftime('%d/%m/%Y') if data_fiscal_obj else '-',
                'Tipo de Lançamento': 'Nota Fiscal',
                'Nº Nota / Doc': nota_fiscal,
                'Cód. Forn.': cod_forn_real,
                'Participante / Favorecido': forn_fiscal,
                'Valor Nota (R$)': val_fiscal_bruto,
                'Valor Saída (R$)': 0.0,
                'Valor Entrada (R$)': 0.0,
                'Status / Classificação Contábil': '❌ Só no Domínio (Falta Saída Bancária)'
            })

    # --- PASSO 2: SOBRAS DO EXTRATO BANCÁRIO ---
    for i, trans in enumerate(extrato_bancario):
        if i not in ids_extrato_usados:
            matriz_final.append({
                'Data de Ref.': trans['Data'],
                'Tipo de Lançamento': 'Recebimento' if trans['Is_Credito'] else 'Pagamento',
                'Nº Nota / Doc': '-',
                'Cód. Forn.': '-',
                'Participante / Favorecido': trans['Fav'],
                'Valor Nota (R$)': 0.0,
                'Valor Saída (R$)': trans['Total'] if not trans['Is_Credito'] else 0.0,
                'Valor Entrada (R$)': trans['Total'] if trans['Is_Credito'] else 0.0,
                'Status / Classificação Contábil': '⚠️ Só no Extrato (Falta Lançar Nota Fiscal)'
            })

    # Exibição dos resultados na interface
    df_resultado = pd.DataFrame(matriz_final)
    
    df_display = df_resultado.copy()
    for col in ['Valor Nota (R$)', 'Valor Saída (R$)', 'Valor Entrada (R$)']:
        df_display[col] = df_display[col].apply(formatar_moeda)

    st.success("🏁 Conciliação processada!")
    st.dataframe(df_display, use_container_width=True)

    # --- EXPORTAÇÃO NATIVA PARA EXCEL (ABRE PERFEITO SEM BUG DE COLUNA UNICA) ---
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_resultado.to_excel(writer, index=False, sheet_name='Conciliação Completa')
    b64 = base64.b64encode(output.getvalue()).decode()
    
    st.download_button(
        label="📥 Baixar Planilha de Conciliação Corrigida (.XLSX)",
        data=output.getvalue(),
        file_name=f"Conciliacao_Unificada_{datetime.now().strftime('%Y%m%d')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
