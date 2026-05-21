import streamlit as st
import pandas as pd
import re
import io
import csv
import warnings
import base64
from datetime import datetime

# Tenta importar bibliotecas extras de forma segura
try:
    import pdfplumber
except ImportError:
    st.error("Erro: A biblioteca 'pdfplumber' não foi encontrada. Instale-a no seu ambiente.")

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
                    texto_bruto = page.extract_text() or ""
                    
                    # MOTOR DUAL-MODE: Trata tabelas complexas do Z.ro Bank (com quebras de linha internas)
                    if '","' in texto_bruto or '\n\n' in texto_bruto:
                        f_io = io.StringIO(texto_bruto)
                        reader = csv.reader(f_io, delimiter=',', quotechar='"')
                        for row in reader:
                            if len(row) < 2: continue
                            
                            sub_datas = [d.strip() for d in row[0].split('\n') if d.strip()]
                            sub_descs = [d.strip() for d in row[1].split('\n') if d.strip()]
                            
                            idx_valor = -2 if len(row) >= 4 else -1
                            sub_valores_raw = [v.strip() for v in row[idx_valor].split('\n') if v.strip()]
                            sub_valores = [v for v in sub_valores_raw if re.search(r'\d+,\d{2}', v)]
                            
                            min_len = min(len(sub_datas), len(sub_descs))
                            for k in range(min_len):
                                dt_txt = sub_datas[k]
                                data_match = re.search(r'(\d{2}/\d{2}/\d{4})', dt_txt)
                                if not data_match: continue
                                
                                desc_txt = sub_descs[k].upper()
                                if any(x in desc_txt for x in ["SALDO INICIAL", "SALDO FINAL", "TOTAL ACUMULADOR", "RESUMO"]): continue
                                if any(t in desc_txt for t in termos_ignorar if t): continue
                                
                                is_credito = any(x in desc_txt for x in ["RECEBID", "DEVOLU", "ESTORNO", "CREDITO", "CRÉDITO", "DEPÓSITO", "TED RECEBIDA"])
                                    
                                val_final = 0.0
                                if k < len(sub_valores):
                                    val_final = abs(limpar_valor(sub_valores[k]))
                                else:
                                    val_match = re.findall(r'-?[\d.]*,\d{2}', row[idx_valor])
                                    if val_match: val_final = abs(limpar_valor(val_match[-1]))
                                        
                                if val_final > 0:
                                    for t in ["PAGAMENTO VIA PIX", "PAGAMENTO DE BOLETO", "TRANSFERENCIA INTERNA", "R$", "RS"]:
                                        desc_txt = desc_txt.replace(t, '')
                                    desc_txt = normalizar_espacos(desc_txt).strip('," ')
                                    
                                    transacoes.append({
                                        'Data': data_match.group(1), 'Total': val_final,
                                        'Fav': desc_txt if desc_txt else "MOVIMENTO BANCARIO", 'Is_Credito': is_credito
                                    })
                    else:
                        # Fallback para formato de texto de linha corrida padrão
                        linhas = texto_bruto.split('\n')
                        for linha in lines:
                            linha_upper = linha.upper()
                            if any(x in linha_upper for x in ["SALDO INICIAL", "SALDO FINAL", "TOTAL ACUMULADOR"]): continue
                            
                            data_match = re.search(r'(\d{2}/\d{2}/\d{4})', linha)
                            valor_match = re.findall(r'-?[\d.]*,\d{2}', linha)
                            
                            if data_match and valor_match:
                                is_credito = any(x in linha_upper for x in ["RECEBID", "DEVOLU", "ESTORNO", "CREDIT"])
                                val = abs(limpar_valor(valor_match[0]))
                                
                                desc_bruta = linha.replace(data_match.group(1), "")
                                for v_txt in valor_match: desc_bruta = desc_bruta.replace(v_txt, "")
                                nome_limpo = normalizar_espacos(desc_bruta).strip('," ')
                                
                                if val > 0:
                                    transacoes.append({
                                        'Data': data_match.group(1), 'Total': val,
                                        'Fav': nome_limpo if nome_limpo else "PAGAMENTO / RECEBIMENTO", 'Is_Credito': is_credito
                                    })
        except Exception as e: 
            st.error(f"Erro ao ler PDF: {e}")
            
    return transacoes

# --- CONFIGURAÇÃO DA BASE DE DADOS ---
BANCO_DE_DADOS_EMPRESAS_INICIAL = {
    "PIXBET SOLUCOES TECNOLOGICAS LTDA": {
        "bancos": {'Z.RO': {'n': 'Z.RO BANK', 'r': '8281458'}},
        "fornecedores": {}
    }
}

if 'empresas_db' not in st.session_state:
    st.session_state['empresas_db'] = BANCO_DE_DADOS_EMPRESAS_INICIAL.copy()

empresa_selecionada = "PIXBET SOLUCOES TECNOLOGICAS LTDA"
config_atual = st.session_state['empresas_db'][empresa_selecionada]

with st.sidebar:
    st.header("⚙️ Parâmetros Contábeis")
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

    extrato_bancario = []
    for f in receipt_files:
        extrato_bancario.extend(extrair_dados_arquivo(f, termos_ignorar))

    # Mapeamento do cabeçalho contábil
    df_dom.columns = [str(c).strip() for c in df_dom.columns]
    col_data = next((c for c in df_dom.columns if re.search(r'data', c, re.IGNORECASE)), None)
    col_valor = next((c for c in df_dom.columns if re.search(r'valor contábil|valor_total|valor', c, re.IGNORECASE)), None)
    col_forn = next((c for c in df_dom.columns if re.search(r'fornecedor|nome', c, re.IGNORECASE)), None)
    col_nota = next((c for c in df_dom.columns if re.search(r'nota|documento', c, re.IGNORECASE)), None)
    
    # Identifica a coluna CÓDIGO correta do Fornecedor por proximidade
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

    # PASSO 1: Processando o arquivo Fiscal
    for idx, row in df_dom.iterrows():
        forn_fiscal = str(row.get(col_forn, ''))
        if pd.isna(row.get(col_forn)) or any(x in forn_fiscal.upper() for x in ["TOTAL", "ACOMPANHAMENTO", "CÓDIGO", "NAN", "NONE"]):
            continue
            
        forn_fiscal_clean = normalizar_espacos(forn_fiscal)
        val_fiscal_bruto = abs(limpar_valor(row.get(col_valor, 0)))
        data_fiscal_obj = converter_data_dominio(row.get(col_data))
        nota_fiscal = str(row.get(col_nota, "-")).split('.')[0]
        cod_forn_real = str(row.get(col_codigo_forn, "-")).split('.')[0] if col_codigo_forn else "-"

        val_irrf = abs(limpar_valor(row.get('Valor', 0))) if 'Valor' in df_dom.columns else 0.0
        val_liquido_esperado = val_fiscal_bruto - val_irrf

        match_banco = None
        for i, trans in enumerate(extrato_bancario):
            if i in ids_extrato_usados: continue
            
            nome_banco_clean = normalizar_espacos(trans['Fav'])
            nome_bate = (forn_fiscal_clean[:12] in nome_banco_clean) or (nome_banco_clean[:12] in forn_fiscal_clean)
            
            try:
                data_banco_obj = datetime.strptime(trans['Data'], '%d/%m/%Y').date()
                dias_dif = abs((data_fiscal_obj - data_banco_obj).days) if data_fiscal_obj else 999
            except: dias_dif = 999
            
            if dias_dif <= tolerancia_dias:
                if val_fiscal_bruto == 0.0 and nome_bate and not trans['Is_Credito']:
                    match_banco = trans
                    ids_extrato_usados.add(i)
                    break
                elif abs(val_fiscal_bruto - trans['Total']) < 0.1 and (nome_bate or val_fiscal_bruto > 5000):
                    match_banco = trans
                    ids_extrato_usados.add(i)
                    break
                elif abs(val_liquido_esperado - trans['Total']) < 0.1 and val_irrf > 0:
                    match_banco = trans
                    ids_extrato_usados.add(i)
                    break

        if match_banco:
            matriz_final.append({
                'Status': '✅ CONCILIADO',
                'Data de Ref.': data_fiscal_obj.strftime('%d/%m/%Y') if data_fiscal_obj else match_banco['Data'],
                'Tipo de Lançamento': 'Misto (NF + Pagamento)' if not match_banco['Is_Credito'] else 'Misto (NF + Recebimento)',
                'Nº Nota / Doc': nota_fiscal,
                'Cód. Forn.': cod_forn_real,
                'Participante / Favorecido': forn_fiscal,
                'Valor Nota (R$)': val_fiscal_bruto,
                'Valor Saída (R$)': match_banco['Total'] if not match_banco['Is_Credito'] else 0.0,
                'Valor Entrada (R$)': match_banco['Total'] if match_banco['Is_Credito'] else 0.0,
                'Status / Classificação Contábil': '✅ CONCILIADO CONTABILMENTE' if val_fiscal_bruto > 0 else '⚠️ CONCILIADO COM VALOR READEQUADO (R$ 0,00)'
            })
        else:
            matriz_final.append({
                'Status': '❌ Só no Domínio',
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

    # PASSO 2: Sobras do Extrato Bancário (Movimentações sem Nota)
    for i, trans in enumerate(extrato_bancario):
        if i not in ids_extrato_usados:
            matriz_final.append({
                'Status': '⚠️ Só no Extrato',
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

    df_resultado = pd.DataFrame(matriz_final)
    
    # Grid Visual Formatado para o ecrã do Streamlit
    df_display = df_resultado.copy()
    for col in ['Valor Nota (R$)', 'Valor Saída (R$)', 'Valor Entrada (R$)']:
        df_display[col] = df_display[col].apply(formatar_moeda)

    st.success("🏁 Conciliação Contábil Processada com Sucesso!")
    st.dataframe(df_display, use_container_width=True)

    # GERAÇÃO NATIVA DO ARQUIVO EXCEL COMPATÍVEL
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_resultado.to_excel(writer, index=False, sheet_name='Conciliação Completa')
    
    st.download_button(
        label="📥 Baixar Planilha de Conciliação (.XLSX)",
        data=output.getvalue(),
        file_name=f"Conciliacao_Unificada_{datetime.now().strftime('%Y%m%d')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
