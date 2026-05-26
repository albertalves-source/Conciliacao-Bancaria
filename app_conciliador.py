import streamlit as st
import pandas as pd
import re
import io
import warnings
import unicodedata
from datetime import datetime

st.set_page_config(page_title="Portal de Conciliação Avançado", layout="wide", page_icon="🏦")
warnings.filterwarnings("ignore")

# --- FUNÇÕES DE APOIO E LIMPEZA ---
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
    txt = re.sub(r'[^A-Z0-9]', '', txt)
    for termo in ["LTDA", "SA", "S/A", "ME", "EIRELI", "SOCIEDADEUNIPESSOAL", "SOLUCOESTECNOLOGICAS", "LTDAME", "DESENVOLVEDORADESISTEMA", "DESENVOLVEDORADESISTEMAS"]:
        txt = txt.replace(termo, "")
    return txt

# --- EXTRATOR DE EXTRATOS ---
def ler_extrato_dinamico(file):
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
        if "NOME CONTRAPARTE" in valores or "DESCRIÇÃO" in valores or "HISTÓRICO" in valores:
            idx_header = i
            break
            
    if idx_header is not None:
        headers = [str(c).strip().upper() for c in df.iloc[idx_header].values]
        dados = df.iloc[idx_header+1:].copy()
        dados.columns = headers
        
        col_data = next((c for c in headers if "DATA" in c), None)
        col_tipo = next((c for c in headers if "TIPO" in c or "NATUREZA" in c), None)
        col_desc_banco = next((c for c in headers if "DESCRI" in c or "HIST" in c), None)
        col_contraparte = next((c for c in headers if "CONTRAPARTE" in c or "FAVORECIDO" in c), None)
        col_valor = next((c for c in headers if "VALOR" in c), None)
        
        for _, r in dados.iterrows():
            if pd.isna(r.get(col_data)) or pd.isna(r.get(col_valor)): continue
            
            dt = converter_data(r[col_data])
            v = limpar_valor(r[col_valor])
            
            nome_final = ""
            if col_contraparte and col_contraparte in r and pd.notna(r[col_contraparte]):
                nome_final = str(r[col_contraparte]).strip()
            
            if not nome_final and col_desc_banco and col_desc_banco in r and pd.notna(r[col_desc_banco]):
                nome_final = str(r[col_desc_banco]).strip()
                
            desc_banco = str(r[col_desc_banco]).strip() if col_desc_banco and pd.notna(r[col_desc_banco]) else ""
            
            if not dt or v == 0: continue
            
            tipo_txt = str(r[col_tipo]).upper() if col_tipo and pd.notna(r[col_tipo]) else ""
            tipo_txt_norm = ''.join(c for c in unicodedata.normalize('NFD', tipo_txt) if unicodedata.category(c) != 'Mn')
            
            is_credito = "CREDITO" in tipo_txt_norm or "ENTRADA" in tipo_txt_norm
            if not col_tipo or tipo_txt == "":
                is_credito = "-" not in str(r[col_valor])
            
            transacoes.append({
                'Data': dt.strftime('%d/%m/%Y'),
                'dt_obj': dt,
                'Valor': v,
                'Razao_Social': nome_final,
                'Desc_Banco': desc_banco,
                'Is_Credito': is_credito
            })
    return transacoes

# --- CARREGADORES DO FISCAL E CADASTRO ---
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
            nome = valores[-1].upper().strip()
            if cod.isdigit():
                mapa[normalizar_para_match(nome)] = cod
    return mapa

def carregar_fiscal_entradas(file):
    file.seek(0)
    if file.name.lower().endswith('.csv'):
        df = pd.read_csv(file, header=None, dtype=str, sep=None, engine='python')
    else:
        df = pd.read_excel(file, header=None, dtype=str)
        
    entradas = []
    for _, row in df.iterrows():
        valores = [str(x).strip() for x in row.values if pd.notna(x)]
        linha_str = " ".join(valores).upper()
        
        if "TOTAL ACUMULADOR" in linha_str or "TOTAL GERAL" in linha_str or "ACOMPANHAMENTO" in linha_str: 
            continue
        
        if len(valores) >= 6:
            dt_nota = None
            for v in valores:
                dt_nota = converter_data(v)
                if dt_nota: break
            
            val_nota = 0.0
            for v in valores:
                if "," in v and v.replace('.','').replace(',','').replace('-','').isdigit():
                    val_nota = limpar_valor(v)
                    break
            
            fornecedor = ""
            for v in valores:
                v_upper = v.upper()
                if any(term in v_upper for term in ["LTDA", "SA", "S/A", "COMERCIO", "TECNOLOGIA", "MARKETING", "SISTEMA", "SERVICOS", "ENTRETENIMENTO", "JUNIOR", "MUNICIPAL"]):
                    fornecedor = re.sub(r'^\d+\.\d+\.\d+[-\s\/]?\d*|^\d{11,14}\s*', '', v_upper).strip()
                    break
            
            nota_num = ""
            if len(valores) > 2 and valores[2].isdigit():
                nota_num = valores[2]
            else:
                match_nota = re.search(r'\b\d{1,13}\b', linha_str)
                if match_nota: nota_num = match_nota.group(0)

            if fornecedor:
                entradas.append({
                    'Fornecedor': fornecedor,
                    'Valor': val_nota,
                    'Nota': nota_num,
                    'Data': dt_nota
                })
    return entradas

def buscar_codigo_conta(nome_pesquisa, mapa_contas):
    norm_pesquisa = normalizar_para_match(nome_pesquisa)
    if not norm_pesquisa: return ""
    
    if "PIXBET" in norm_pesquisa:
        return "1121"
        
    if norm_pesquisa in mapa_contas:
        return mapa_contas[norm_pesquisa]
        
    for nome_cad, cod in mapa_contas.items():
        if norm_pesquisa in nome_cad or nome_cad in norm_pesquisa:
            return cod
            
    if len(norm_pesquisa) >= 4:
        for nome_cad, cod in mapa_contas.items():
            if nome_cad.startswith(norm_pesquisa[:6]) or norm_pesquisa.startswith(nome_cad[:6]):
                return cod
    return ""

# --- INTERFACE STREAMLIT ---
with st.sidebar:
    st.header("⚙️ Parâmetros Contábeis")
    cod_banco = st.text_input("Código da Conta Bancária (Empresa):", value="2139")
    conta_padrao_receita = st.text_input("Conta de Recebimento Padrão:", value="1121")

tab1, tab2 = st.tabs(["🔄 1. Nova Conciliação (Completa)", "📤 2. Gerar TXT de Planilha Auditada"])

# --- ABA 1 ---
with tab1:
    st.markdown("### Processar Arquivos Brutos")
    colA, colB, colC = st.columns(3)
    with colA: f_extratos = st.file_uploader("📂 Extrato Bancário", type=["xlsx","csv","pdf"], accept_multiple_files=True, key="ext1")
    with colB: f_contas = st.file_uploader("🗂️ Arquivo de Contas (FLABET)", type=["xlsx","csv"], key="cont1")
    with colC: f_entradas = st.file_uploader("📥 Relatório de Entradas (Fiscal)", type=["xlsx","csv"], key="fisc1")

    if f_extratos and f_contas and f_entradas:
        mapa_contas = carregar_cadastro_contas(f_contas)
        cadastro_entradas = carregar_fiscal_entradas(f_entradas)
        
        extrato_lista = []
        for f in f_extratos:
            extrato_lista.extend(ler_extrato_dinamico(f))
            
        matriz_conciliada = []
        
        for tx in extrato_lista:
            codigo_fornecedor = buscar_codigo_conta(tx['Razao_Social'], mapa_contas)
            
            if tx['Is_Credito']:
                c_deb = cod_banco
                c_crd = codigo_fornecedor if codigo_fornecedor else conta_padrao_receita
                
                if "TRANSFERENCIA" in tx['Desc_Banco'].upper() or "PIXBET" in tx['Razao_Social'].upper():
                    hist_final = "RECB TRANSFERENCIA INTERNA ENTRE CONTAS"
                else:
                    hist_final = f"RECB {tx['Razao_Social']}"
            else:
                c_deb = codigo_fornecedor if codigo_fornecedor else "CONTA_MANUAL"
                c_crd = cod_banco
                
                nota_vinculada = ""
                norm_tx_nome = normalizar_para_match(tx['Razao_Social'])
                
                for ent in cadastro_entradas:
                    norm_ent_nome = normalizar_para_match(ent['Fornecedor'])
                    if norm_tx_nome and (norm_tx_nome in norm_ent_nome or norm_ent_nome in norm_tx_nome):
                        nota_vinculada = ent['Nota']
                        break
                
                if nota_vinculada:
                    hist_final = f"PAGTO NF {nota_vinculada} {tx['Razao_Social']}"
                else:
                    hist_final = f"PAGTO {tx['Razao_Social']}"
                    
            matriz_conciliada.append({
                'Data': tx['Data'],
                'Deb': c_deb,
                'Cred': c_crd,
                'Valor': formatar_moeda_br(tx['Valor']),
                'Histórico': " ".join(hist_final.upper().split())
            })
            
        df_final = pd.DataFrame(matriz_conciliada)
        
        if not df_final.empty:
            st.success("Conciliação Pré-Processada com Sucesso!")
            st.dataframe(df_final, use_container_width=True)
            
            st.markdown("---")
            st.markdown("### 📥 Escolha como deseja exportar:")
            
            col_btn1, col_btn2 = st.columns(2)
            
            with col_btn1:
                # NOVA FUNCIONALIDADE: Geração e Download em Excel (.xlsx) para ajuste humano
                output_excel = io.BytesIO()
                with pd.ExcelWriter(output_excel, engine='openpyxl') as writer:
                    df_final.to_excel(writer, index=False, sheet_name='Conciliacao_Analise')
                
                st.download_button(
                    label="📥 1. Baixar Planilha para Ajustes (.XLSX)",
                    data=output_excel.getvalue(),
                    file_name=f"Analise_Humana_Conciliacao_{datetime.now().strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
                
            with col_btn2:
                # Geração do Arquivo TXT delimitado por Tabulação direto
                output_txt = io.StringIO()
                for _, r in df_final.iterrows():
                    output_txt.write(f"{r['Data']}\t{r['Deb']}\t{r['Cred']}\t{r['Valor']}\t{r['Histórico']}\n")
                    
                st.download_button(
                    label="📄 2. Gerar Arquivo de Importação Direta (.TXT)",
                    data=output_txt.getvalue().encode('utf-8'),
                    file_name=f"Importacao_Dominio_Direto_{datetime.now().strftime('%Y%m%d')}.txt",
                    mime="text/plain",
                    use_container_width=True
                )

# --- ABA 2 ---
with tab2:
    st.markdown("### Gerar TXT de Planilha Prontamente Editada / Auditada")
    st.info("Suba aqui a planilha .xlsx que você baixou na Aba 1 e corrigiu manualmente.")
    f_editado = st.file_uploader("📥 Anexe a planilha auditada (.xlsx)", type=["xlsx"], key="edit2")
    if f_editado:
        df_audit = pd.read_excel(f_editado, dtype=str)
        cols = {str(c).upper().strip(): c for c in df_audit.columns}
        
        c_dt = cols.get('DATA')
        c_db = cols.get('DEB') or cols.get('CONTA_DEBITO') or cols.get('CONTA_DÉBITO')
        c_cr = cols.get('CRED') or cols.get('CONTA_CREDITO') or cols.get('CONTA_CRÉDITO')
        c_vl = cols.get('VALOR') or cols.get('SAÍDAS')
        c_hs = cols.get('HISTÓRICO') or cols.get('HISTORICO')
        
        if c_dt and c_vl and c_hs:
            st.success("Planilha processada e pronta para conversão contábil!")
            txt_output_audit = io.StringIO()
            
            for _, row in df_audit.iterrows():
                dt_f = str(row[c_dt]).strip().split(' ')[0]
                val_f = str(row[c_vl]).strip()
                if not val_f.startswith('R$'):
                    val_f = formatar_moeda_br(limpar_valor(val_f))
                
                txt_output_audit.write(f"{dt_f}\t{str(row[c_db]).split('.')[0] if c_db and pd.notna(row[c_db]) else ''}\t{str(row[c_cr]).split('.')[0] if c_cr and pd.notna(row[c_cr]) else ''}\t{val_f}\t{str(row[c_hs]).upper().strip()}\n")
                
            st.download_button(
                label="📄 Baixar Arquivo Domínio Formatado (.TXT)",
                data=txt_output_audit.getvalue().encode('utf-8'),
                file_name=f"Importacao_Dominio_Editado_{datetime.now().strftime('%Y%m%d')}.txt",
                mime="text/plain",
                use_container_width=True
            )
