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
    # Remove acentos
    txt = ''.join(c for c in unicodedata.normalize('NFD', txt) if unicodedata.category(c) != 'Mn')
    # Remove pontuações e caracteres especiais, mantendo apenas letras e números
    txt = re.sub(r'[^A-Z0-9]', '', txt)
    # Remove termos comuns que causam distorção no cruzamento
    for termo in ["LTDA", "SA", "S/A", "ME", "EIRELI", "SOCIEDADEUNIPESSOAL", "SOLUCOESTECNOLOGICAS"]:
        txt = txt.replace(termo, "")
    return txt

# --- EXTRATOR INTELIGENTE DE EXTRATOS (CSV, EXCEL, PDF) ---
def ler_extrato_dinamico(file):
    file.seek(0)
    if file.name.lower().endswith('.csv'):
        try: df = pd.read_csv(file, header=None, dtype=str, sep=None, engine='python')
        except: df = pd.read_csv(file, header=None, dtype=str)
    else:
        df = pd.read_excel(file, header=None, dtype=str)
    
    transacoes = []
    idx_header = None
    
    # Identifica o cabeçalho correto com base no layout da Celcoin ou outros bancos
    for i, row in df.iterrows():
        valores = [str(x).strip().upper() for x in row.values if pd.notna(x)]
        if "NOME CONTRAPARTE" in valores or "DESCRIÇÃO" in valores or "HISTÓRICO" in valores:
            idx_header = i
            break
            
    if idx_header is not None:
        headers = [str(c).strip().upper() for c in df.iloc[idx_header].values]
        dados = df.iloc[idx_header+1:].copy()
        dados.columns = headers
        
        # Mapeamento de colunas com base no arquivo Celcoin enviado
        col_data = next((c for c in headers if "DATA" in c), None)
        col_tipo = next((c for c in headers if "TIPO" in c or "NATUREZA" in c), None)
        col_desc_banco = next((c for c in headers if "DESCRI" in c), None)
        col_contraparte = next((c for c in headers if "CONTRAPARTE" in c or "FAVORECIDO" in c), None)
        col_valor = next((c for c in headers if "VALOR" in c), None)
        
        for _, r in dados.iterrows():
            if pd.isna(r.get(col_data)) or pd.isna(r.get(col_valor)): continue
            
            dt = converter_data(r[col_data])
            v = limpar_valor(r[col_valor])
            
            # Se o nome da contraparte estiver preenchido, usa ele, caso contrário usa a descrição básica
            nome_final = str(r[r.get(col_contraparte)] if col_contraparte and pd.notna(r.get(col_contraparte)) else r.get(col_desc_banco, "")).strip()
            desc_banco = str(r.get(col_desc_banco, "")).strip()
            
            if not dt or v == 0: continue
            
            is_credito = str(r.get(col_tipo, "")).upper() == "CRÉDITO" or str(r.get(col_tipo, "")).upper() == "CREDITO"
            
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
            nome = valores[-1].upper().strip() # Pega a Razão Social da última coluna preenchida
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
        linha_str = " ".join([str(x).upper() for x in row.values if pd.notna(x)])
        # Filtra linhas que possuem dados fiscais de fornecedor reais
        if "TOTAL ACUMULADOR" in linha_str or "TOTAL GERAL" in linha_str: continue
        
        valores = [str(x).strip() for x in row.values if pd.notna(x)]
        if len(valores) >= 7:
            # Captura a data da nota
            dt_nota = None
            for v in valores:
                dt_nota = converter_data(v)
                if dt_nota: break
            
            # Localiza o valor contábil e o nome do fornecedor na linha estruturada
            val_nota = 0.0
            for v in valores:
                if "," in v and v.replace('.','').replace(',','').isdigit():
                    val_nota = limpar_valor(v)
                    break
            
            # Captura o nome do Fornecedor (removendo CPFs/CNPJs que venham colados na mesma célula)
            fornecedor = ""
            for v in valores:
                if any(term in v.upper() for term in ["LTDA", "SA", "S/A", "COMERCIO", "TECNOLOGIA", "MARKETING", "SISTEMA", "SERVICOS", "ENTRETENIMENTO"]):
                    fornecedor = re.sub(r'^\d+\.\d+\.\d+[-\s\/]?\d*|^\d{11,14}\s*', '', v.upper()).strip()
                    break
            
            # Número da Nota Fiscal
            nota_num = ""
            match_nota = re.search(r'\b\d{1,13}\b', " ".join(valores))
            if match_nota:
                nota_num = match_nota.group(0)

            if fornecedor and val_nota > 0:
                entradas.append({
                    'Fornecedor': fornecedor,
                    'Valor': val_nota,
                    'Nota': nota_num,
                    'Data': dt_nota
                })
    return entradas

# --- BUSCA DE CONTAS INTELIGENTE POR PROXIMIDADE ---
def buscar_codigo_conta(nome_pesquisa, mapa_contas):
    norm_pesquisa = normalizar_para_match(nome_pesquisa)
    if not norm_pesquisa: return ""
    
    # 1. Match Perfeito
    if norm_pesquisa in mapa_contas:
        return mapa_contas[norm_pesquisa]
        
    # 2. Match por contido/contém (Ex: "CRAB DE BURGOS" localiza "CRAB DE BURGOS SOCIEDADE UNIPESSOAL LTDA")
    for nome_cad, cod in mapa_contas.items():
        if norm_pesquisa in nome_cad or nome_cad in norm_pesquisa:
            return cod
            
    # 3. Match parcial por palavras iniciais significas (comprimento >= 5)
    if len(norm_pesquisa) >= 5:
        for nome_cad, cod in mapa_contas.items():
            if nome_cad.startswith(norm_pesquisa[:8]) or norm_pesquisa.startswith(nome_cad[:8]):
                return cod
    return ""

# --- INTERFACE FLUXO DE TRABALHO ---
with st.sidebar:
    st.header("⚙️ Parâmetros Contábeis")
    cod_banco = st.text_input("Código da Conta Bancária (Empresa):", value="1857")
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
                # Regra de Crédito: Banco recebe o Débito
                c_deb = cod_banco
                c_crd = codigo_fornecedor if codigo_fornecedor else conta_padrao_receita
                
                if "TRANSFERENCIA" in tx['Desc_Banco'].upper():
                    hist_final = "RECB TRANSFERENCIA INTERNA ENTRE CONTAS"
                else:
                    hist_final = f"RECB {tx['Razao_Social']}"
            else:
                # Regra de Débito: Banco recebe o Crédito
                c_deb = codigo_fornecedor if codigo_fornecedor else ""
                c_crd = cod_banco
                
                # Tenta localizar Nota Fiscal no relatório de Entradas associando o valor exato e o nome do parceiro
                nota_vinculada = ""
                norm_tx_nome = normalizar_para_match(tx['Razao_Social'])
                for ent in cadastro_entradas:
                    if abs(ent['Valor'] - tx['Valor']) <= 0.01:
                        norm_ent_nome = normalizar_para_match(ent['Fornecedor'])
                        if norm_tx_nome in norm_ent_nome or norm_ent_nome in norm_tx_nome:
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
                'Valor_Raw': tx['Valor'],
                'Valor': formatar_moeda_br(tx['Valor']),
                'Histórico': " ".join(hist_final.upper().split())
            })
            
        df_final = pd.DataFrame(matriz_conciliada)
        
        if not df_final.empty:
            st.success("Conciliação Realizada com Sucesso!")
            st.dataframe(df_final[['Data', 'Deb', 'Cred', 'Valor', 'Histórico']], use_container_width=True)
            
            # Exportador TXT estruturado estritamente em Tabulação
            output_txt = io.StringIO()
            for _, r in df_final.iterrows():
                output_txt.write(f"{r['Data']}\t{r['Deb']}\t{r['Cred']}\t{r['Valor']}\t{r['Histórico']}\n")
                
            st.download_button(
                label="📄 Baixar Arquivo de Conciliação Final (.TXT)",
                data=output_txt.getvalue().encode('utf-8'),
                file_name=f"Importacao_Dominio_{datetime.now().strftime('%Y%m%d')}.txt",
                mime="text/plain",
                use_container_width=True
            )

# --- ABA 2 ---
with tab2:
    st.markdown("### Gerar TXT de Planilha Prontamente Editada")
    f_editado = st.file_uploader("📥 Enexe a planilha auditada (.xlsx)", type=["xlsx"], key="edit2")
    if f_editado:
        df_audit = pd.read_excel(f_editado, dtype=str)
        cols = {str(c).upper().strip(): c for c in df_audit.columns}
        
        c_dt = cols.get('DATA')
        c_db = cols.get('DEB') or cols.get('CONTA_DEBITO') or cols.get('CONTA_DÉBITO')
        c_cr = cols.get('CRED') or cols.get('CONTA_CREDITO') or cols.get('CONTA_CRÉDITO')
        c_vl = cols.get('VALOR') or cols.get('SAÍDAS')
        c_hs = cols.get('HISTÓRICO') or cols.get('HISTORICO')
        
        if c_dt and c_vl and c_hs:
            st.success("Planilha processada!")
            txt_output_audit = io.StringIO()
            
            for _, row in df_audit.iterrows():
                dt_f = str(row[c_dt]).strip().split(' ')[0]
                val_f = str(row[c_vl]).strip()
                if not val_f.startswith('R$'):
                    val_f = formatar_moeda_br(limpar_valor(val_f))
                
                txt_output_audit.write(f"{dt_f}\t{str(row[c_db]).split('.')[0] if c_db else ''}\t{str(row[c_cr]).split('.')[0] if c_cr else ''}\t{val_f}\t{str(row[c_hs]).upper().strip()}\n")
                
            st.download_button(
                label="📄 Baixar TXT da Planilha Auditada",
                data=txt_output_audit.getvalue().encode('utf-8'),
                file_name=f"Importacao_Dominio_Editado_{datetime.now().strftime('%Y%m%d')}.txt",
                mime="text/plain",
                use_container_width=True
            )
