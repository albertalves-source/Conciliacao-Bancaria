import streamlit as st
import pandas as pd
import re
import io
import warnings
import unicodedata
from datetime import datetime

st.set_page_config(page_title="Portal de Conciliação Avançado", layout="wide", page_icon="🏦")
warnings.filterwarnings("ignore")

# --- FUNÇÕES DE APOIO ---
def formatar_moeda_br(v):
    try:
        val = float(v)
        return f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except: return "R$ 0,00"

def limpar_valor(v):
    if pd.isna(v): return 0.0
    v_str = str(v).replace('R$', '').replace('$', '').replace(' ', '').strip()
    if '.' in v_str and ',' in v_str:
        v_str = v_str.replace('.', '').replace(',', '.')
    elif ',' in v_str:
        v_str = v_str.replace(',', '.')
    try: 
        return abs(float(v_str))
    except: 
        return 0.0

def converter_data(data_obj):
    if pd.isna(data_obj): return None
    s = str(data_obj).strip().split(' ')[0]
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try: return datetime.strptime(s, fmt).date()
        except: pass
    try:
        num = float(s)
        if num > 10000: return pd.to_datetime(num, unit='D', origin='1899-12-30').date()
    except: pass
    return None

def normalizar_para_match(texto):
    if not texto: return ""
    txt = str(texto).upper().strip()
    txt = ''.join(c for c in unicodedata.normalize('NFD', txt) if unicodedata.category(c) != 'Mn')
    txt = re.sub(r'[\s\-,.\/]', '', txt)
    return txt

# --- PARSERS DE EXTRATOS DINÂMICOS ---
def ler_extrato_dataframe(file):
    file.seek(0)
    if file.name.lower().endswith('.csv'):
        try: df = pd.read_csv(file, header=None, dtype=str, sep=None, engine='python')
        except: df = pd.read_csv(file, header=None, dtype=str)
    else:
        df = pd.read_excel(file, header=None, dtype=str)
    
    transacoes = []
    idx_header = None
    
    for i, row in df.iterrows():
        valores = [str(x).strip().upper() for x in row.values if pd.notna(x)]
        if any(col in valores for col in ["DESCRIÇÃO", "HISTÓRICO", "DESCRICAO", "FAVORECIDO", "NOME CONTRAPARTE"]):
            idx_header = i
            break
            
    if idx_header is not None:
        headers = [str(c).strip().upper() for c in df.iloc[idx_header].values]
        dados = df.iloc[idx_header+1:].copy()
        dados.columns = headers
        
        col_data = next((c for c in headers if "DATA" in c), None)
        col_desc = next((c for c in headers if "DESC" in c or "HIST" in c or "NOME CONTRAPARTE" in c), None)
        col_valor = next((c for c in headers if "VALOR" in c or "QUANTIA" in c), None)
        col_tipo = next((c for c in headers if "TIPO" in c or "NATUREZA" in c), None)
        
        if col_data and col_desc and col_valor:
            for _, r in dados.iterrows():
                dt = converter_data(r[col_data])
                v = limpar_valor(r[col_valor])
                desc = str(r[col_desc]).strip() if pd.notna(r[col_desc]) else ""
                
                if not dt or v == 0: continue
                if any(x in desc.upper() for x in ["SALDO", "TOTAL", "RESUMO"]): continue
                
                tipo_txt = str(r[col_tipo]).upper() if col_tipo and pd.notna(r[col_tipo]) else ""
                is_credito = "CRÉDITO" in tipo_txt or "CREDITO" in tipo_txt or "C" == tipo_txt or "ENTRADA" in tipo_txt
                if not col_tipo:
                    is_credito = "-" not in str(r[col_valor])
                
                transacoes.append({
                    'Data': dt.strftime('%d/%m/%Y'),
                    'dt_obj': dt,
                    'Valor': v,
                    'Descricao': desc,
                    'Is_Credito': is_credito
                })
    return transacoes

def ler_extrato_pdf(file):
    transacoes = []
    try:
        import pdfplumber
        with pdfplumber.open(file) as pdf:
            for page in pdf.pages:
                texto = page.extract_text() or ""
                for linha in texto.split('\n'):
                    linha_upper = linha.upper()
                    if any(x in linha_upper for x in ["SALDO INICIAL", "SALDO FINAL", "TOTAL"]): continue
                    
                    data_match = re.search(r'(\d{2}/\d{2}/\d{4})|(\d{4}-\d{2}-\d{2})', linha)
                    valor_match = re.findall(r'-?[\d\s\.]*,\d{2}', linha)
                    
                    if data_match and valor_match:
                        dt = converter_data(data_match.group(0))
                        v = limpar_valor(valor_match[-1])
                        
                        if not dt or v == 0: continue
                        
                        is_credito = "RECEB" in linha_upper or "CREDIT" in linha_upper or "ESTORNO" in linha_upper or "+" in linha
                        if "-" in valor_match[-1]: is_credito = False
                        
                        desc = linha.replace(data_match.group(0), "")
                        for vm in valor_match: desc = desc.replace(vm, "")
                        desc = " ".join(desc.strip().split())
                        
                        transacoes.append({
                            'Data': dt.strftime('%d/%m/%Y'),
                            'dt_obj': dt,
                            'Valor': v,
                            'Descricao': desc,
                            'Is_Credito': is_credito
                        })
    except Exception as e:
        st.error(f"Erro ao processar PDF: {e}")
    return transacoes

def carregar_cadastro_contas(file):
    file.seek(0)
    if file.name.lower().endswith('.csv'):
        df = pd.read_csv(file, header=None, dtype=str, sep=None, engine='python')
    else:
        df = pd.read_excel(file, header=None, dtype=str)
    
    mapa = {}
    for _, r in df.iterrows():
        valores = [str(x).strip() for x in r.values if pd.notna(x)]
        if len(valores) >= 2:
            cod = valores[0].split('.')[0]
            nome = valores[1].upper().strip()
            if cod.isdigit():
                mapa[normalizar_para_match(nome)] = cod
    return mapa

def carregar_fiscal_entradas(file):
    file.seek(0)
    if file.name.lower().endswith('.csv'):
        df = pd.read_csv(file, header=None, dtype=str, sep=None, engine='python')
    else:
        df = pd.read_excel(file, header=None, dtype=str)
        
    idx_header = 0
    for i, row in df.iterrows():
        valores = [str(x).strip().upper() for x in row.values if pd.notna(x)]
        if "FORNECEDOR" in valores or "VALOR CONTÁBIL" in valores:
            idx_header = i
            break
            
    dados = df.iloc[idx_header+1:].copy()
    headers = [str(c).strip().upper() for c in df.iloc[idx_header].values]
    
    col_forn = next((i for i, c in enumerate(headers) if "FORNECEDOR" in c), None)
    col_valor = next((i for i, c in enumerate(headers) if "VALOR" in c), None)
    col_nota = next((i for i, c in enumerate(headers) if "NOTA" in c or "DOC" in c), None)
    col_data = next((i for i, c in enumerate(headers) if "DATA" in c), None)
    
    entradas = []
    for _, r in dados.iterrows():
        if col_forn is not None and pd.notna(r.iloc[col_forn]):
            entradas.append({
                'Fornecedor': str(r.iloc[col_forn]).strip().upper(),
                'Valor': limpar_valor(r.iloc[col_valor]) if col_valor is not None else 0.0,
                'Nota': str(r.iloc[col_nota]).split('.')[0] if col_nota is not None else "",
                'Data': converter_data(r.iloc[col_data]) if col_data is not None else None
            })
    return entradas

# --- CONFIGURAÇÕES DA SIDEBAR ---
with st.sidebar:
    st.header("⚙️ Configurações da Conta")
    cod_banco = st.text_input("Código da Conta Bancária (Empresa):", value="1857")
    conta_padrao_receita = st.text_input("Código de Conta de Recebimento Padrão:", value="1121")

# --- DEFINIÇÃO DAS ABAS ---
tab1, tab2 = st.tabs(["🔄 1. Nova Conciliação", "📤 2. Gerar TXT de Planilha Editada/Auditada"])

# --- ABA 1: PROCESSO COMPLETO ---
with tab1:
    st.markdown("### Processe novos relatórios e faça o cruzamento inicial")
    f_extrato = st.file_uploader("📂 1. Anexe o Extrato Bancário (PDF, Excel ou CSV)", type=["pdf", "xlsx", "csv"], accept_multiple_files=True, key="ext_tab1")
    f_contas = st.file_uploader("🗂️ 2. Anexe o Arquivo de Códigos das Contas", type=["xlsx", "csv"], key="contas_tab1")
    f_entradas = st.file_uploader("📥 3. Anexe o Relatório de Entradas/Notas Fiscais (Opcional)", type=["xlsx", "csv"], key="fiscal_tab1")

    if f_extrato and f_contas:
        mapa_contas = carregar_cadastro_contas(f_contas)
        cadastro_entradas = carregar_fiscal_entradas(f_entradas) if f_entradas else []
        
        todas_transacoes = []
        for ext in f_extrato:
            if ext.name.lower().endswith('.pdf'):
                todas_transacoes.extend(ler_extrato_pdf(ext))
            else:
                todas_transacoes.extend(ler_extrato_dataframe(ext))
                
        resultado_final = []
        
        for tx in todas_transacoes:
            desc_norm = normalizar_para_match(tx['Descricao'])
            conta_encontrada = ""
            
            for nome_cad, cod_cad in mapa_contas.items():
                if nome_cad in desc_norm or desc_norm in nome_cad:
                    conta_encontrada = cod_cad
                    break
                    
            if tx['Is_Credito']:
                conta_debito = cod_banco
                conta_credito = conta_encontrada if conta_encontrada else conta_padrao_receita
                historico = f"RECB TRANSFERENCIA INTERNA ENTRE CONTAS" if "TRANSFERENCIA" in tx['Descricao'].upper() else f"RECB {tx['Descricao']}"
            else:
                conta_debito = conta_encontrada if conta_encontrada else ""
                conta_credito = cod_banco
                
                nota_fiscal = ""
                for ent in cadastro_entradas:
                    if ent['Valor'] == tx['Valor'] and (normalizar_para_match(ent['Fornecedor']) in desc_norm or desc_norm in normalizar_para_match(ent['Fornecedor'])):
                        nota_fiscal = ent['Nota']
                        break
                        
                historico = f"PAGTO NF {nota_fiscal} {tx['Descricao']}" if nota_fiscal else f"PAGTO {tx['Descricao']}"
                    
            resultado_final.append({
                'Data': tx['Data'],
                'Conta_Débito': conta_debito,
                'Conta_Crédito': conta_credito,
                'Valor': formatar_moeda_br(tx['Valor']),
                'Histórico': " ".join(historico.upper().split())
            })
            
        df_resultado = pd.DataFrame(resultado_final)
        
        if not df_resultado.empty:
            st.success("Conciliação gerada com sucesso!")
            st.dataframe(df_resultado, use_container_width=True)
            
            # Download da Planilha Excel caso queira editar antes de gerar o TXT definitivo
            output_excel = io.BytesIO()
            with pd.ExcelWriter(output_excel, engine='openpyxl') as writer:
                df_resultado.to_excel(writer, index=False, sheet_name='Conciliacao')
            
            st.download_button(
                label="📥 Baixar esta prévia em Excel (.XLSX) para Auditoria",
                data=output_excel.getvalue(),
                file_name=f"Previa_Conciliacao_{datetime.now().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
            
            # Download direto do TXT padronizado
            buffer_txt = io.StringIO()
            for _, row in df_resultado.iterrows():
                buffer_txt.write(f"{row['Data']}\t{row['Conta_Débito']}\t{row['Conta_Crédito']}\t{row['Valor']}\t{row['Histórico']}\n")
            
            st.download_button(
                label="📄 Gerar Arquivo de Importação (.TXT) Direto",
                data=buffer_txt.getvalue().encode('utf-8'),
                file_name=f"Importacao_Direta_{datetime.now().strftime('%Y%m%d')}.txt",
                mime="text/plain",
                use_container_width=True
            )

# --- ABA 2: PROCESSAR PLANILHA JÁ EDITADA ---
with tab2:
    st.markdown("### Importe uma planilha Excel (.xlsx) corrigida manualmente para gerar o TXT")
    st.info("A planilha importada deve conter as colunas de dados prontas com os títulos idênticos ou equivalentes a: 'Data', 'Conta_Débito', 'Conta_Crédito', 'Valor' e 'Histórico'.")
    
    f_editada = st.file_uploader("📥 Anexe a Planilha Excel Editada/Auditada", type=["xlsx"], key="excel_editado")
    
    if f_editada:
        try:
            df_auditado = pd.read_excel(f_editada, dtype=str)
            
            # Detectar colunas de forma tolerante a maiúsculas/minúsculas/acentos
            cols = {c.upper().strip(): c for c in df_auditado.columns}
            
            col_dt = cols.get('DATA')
            col_deb = cols.get('CONTA_DÉBITO') or cols.get('CONTA_DEBITO') or cols.get('DEB')
            col_cred = cols.get('CONTA_CRÉDITO') or cols.get('CONTA_CREDITO') or cols.get('CRED')
            col_val = cols.get('VALOR') or cols.get('SAÍDAS') or cols.get('SAIDAS')
            col_hist = cols.get('HISTÓRICO') or cols.get('HISTORICO')
            
            if col_dt and col_val and col_hist:
                st.success("Planilha carregada com sucesso! Veja uma prévia dos dados mapeados:")
                
                # Montar DataFrame padronizado para exibição
                df_txt_builder = pd.DataFrame({
                    'Data': df_auditado[col_dt],
                    'Conta_Débito': df_auditado[col_deb] if col_deb else "",
                    'Conta_Crédito': df_auditado[col_cred] if col_cred else "",
                    'Valor': df_auditado[col_val],
                    'Histórico': df_auditado[col_hist]
                }).fillna("")
                
                st.dataframe(df_txt_builder, use_container_width=True)
                
                # Gerar String com separador TAB de acordo com seu layout
                buffer_auditado_txt = io.StringIO()
                for _, row in df_txt_builder.iterrows():
                    # Garante formatação correta de strings limpas
                    dt_str = str(row['Data']).strip().split(' ')[0]
                    val_str = str(row['Valor']).strip()
                    # Se o valor veio como float puro no excel, tenta garantir que tenha o 'R$' se você preferir
                    if not val_str.startswith('R$') and val_str.replace('.','',1).replace(',','',1).isdigit():
                        val_str = formatar_moeda_br(limpar_valor(val_str))
                        
                    buffer_auditado_txt.write(f"{dt_str}\t{str(row['Conta_Débito']).split('.')[0]}\t{str(row['Conta_Crédito']).split('.')[0]}\t{val_str}\t{str(row['Histórico']).upper().strip()}\n")
                
                st.download_button(
                    label="📄 Baixar Arquivo Domínio Formatado (.TXT) da Planilha Auditada",
                    data=buffer_auditado_txt.getvalue().encode('utf-8'),
                    file_name=f"Importacao_Planilha_Auditada_{datetime.now().strftime('%Y%m%d')}.txt",
                    mime="text/plain",
                    use_container_width=True
                )
            else:
                st.error("Não encontramos colunas essenciais como 'Data', 'Valor' e 'Histórico' no arquivo enviado. Verifique os títulos das colunas.")
        except Exception as e:
            st.error(f"Erro ao processar a planilha auditada: {e}")
