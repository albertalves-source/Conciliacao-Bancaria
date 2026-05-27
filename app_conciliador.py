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

def formatar_valor_dominio(v):
    """Formata o valor estritamente para o TXT do Domínio: sem pontos de milhar e com vírgula decimal"""
    try:
        val = limpar_valor(v)
        return f"{val:.2f}".replace('.', ',')
    except:
        return "0,00"

def converter_data(data_obj):
    if pd.isna(data_obj): return None
    s = str(data_obj).strip().split(' ')[0]
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y%m%d'):
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

# --- MOTOR DETECTOR CORRIGIDO: INDIVIDUALIZAÇÃO REAL DOS 3 BANCOS ---
def descobrir_codigo_banco_do_extrato(df_bruto, configuracao_bancos, nome_arquivo):
    texto_cabecalho = ""
    for _, row in df_bruto.head(30).iterrows():
        texto_cabecalho += " " + " ".join([str(x).upper() for x in row.values if pd.notna(x)])
    
    nome_arq_upper = nome_arquivo.upper()
    
    # 1. Checa se é SICOOB (no conteúdo ou no nome do arquivo) -> Retorna 2093
    if "SICOOB" in texto_cabecalho or "SICOOB" in nome_arq_upper:
        return configuracao_bancos["SICOOB"]
        
    # 2. Checa se é DELBANK/DELFINANCE -> Retorna 1110
    elif any(x in texto_cabecalho for x in ["DELBANK", "DELFINANCE", "DEL FINANCE"]) or any(x in nome_arq_upper for x in ["DELBANK", "DELFINANCE", "DELF"]):
        return configuracao_bancos["DELBANK/DELFINANCE"]
        
    # 3. Checa se é CELCOIN -> Retorna 1868
    elif "CELCOIN" in texto_cabecalho or "CELCOIN" in nome_arq_upper:
        return configuracao_bancos["CELCOIN"]
            
    # Se falhar totalmente no mapeamento automático, aciona a ajuda na tela
    return f"PEDIR_AJUDA_DE_{nome_arquivo}"

# --- EXTRATOR DE EXTRATOS TRATADO ---
def ler_extrato_dinamico(file, configuracao_bancos):
    file.seek(0)
    conteudo = file.read()
    
    if file.name.lower().endswith('.csv'):
        try: df = pd.read_csv(io.StringIO(conteudo.decode('utf-8')), header=None, dtype=str, sep=None, engine='python')
        except: 
            try: df = pd.read_csv(io.StringIO(conteudo.decode('iso-8859-1')), header=None, dtype=str, sep=None, engine='python')
            except: df = pd.read_csv(io.BytesIO(conteudo), header=None, dtype=str)
    else:
        df = pd.read_excel(io.BytesIO(conteudo), header=None, dtype=str)
    
    # Identifica o banco de forma isolada para este arquivo
    cod_banco_identificado = descobrir_codigo_banco_do_extrato(df, configuracao_bancos, file.name)
    
    transacoes = []
    idx_header = None
    
    for i, row in df.iterrows():
        valores = [str(x).strip().upper() for x in row.values if pd.notna(x)]
        if any(term in valores for term in ["NOME CONTRAPARTE", "DESCRIÇÃO", "HISTÓRICO", "DESCRICAO", "FAVORECIDO", "VALOR", "DOCUMENTO"]):
            idx_header = i
            break
            
    if idx_header is not None:
        headers = [str(c).strip().upper() for c in df.iloc[idx_header].values]
        dados = df.iloc[idx_header+1:].copy()
        dados.columns = headers
        
        col_data = next((c for c in headers if "DATA" in c), None)
        col_tipo = next((c for c in headers if "TIPO" in c or "NATUREZA" in c or "DEB/CRED" in c), None)
        col_desc_banco = next((c for c in headers if "DESCRI" in c or "HIST" in c), None)
        col_contraparte = next((c for c in headers if "CONTRAPARTE" in c or "FAVORECIDO" in c or "NOME" in c), None)
        col_valor = next((c for c in headers if "VALOR" in c or "QUANTIA" in c), None)
        
        for _, r in dados.iterrows():
            if pd.isna(r.get(col_data)) or pd.isna(r.get(col_valor)): continue
            
            dt = converter_data(r[col_data])
            v = limpar_valor(r[col_valor])
            if v == 0 or not dt: continue
            
            nome_final = ""
            if col_contraparte and col_contraparte in r and pd.notna(r[col_contraparte]):
                nome_final = str(r[col_contraparte]).strip()
            
            if not nome_final and col_desc_banco and col_desc_banco in r and pd.notna(r[col_desc_banco]):
                nome_final = str(r[col_desc_banco]).strip()
                
            desc_banco = str(r[col_desc_banco]).strip() if col_desc_banco and pd.notna(r[col_desc_banco]) else ""
            
            tipo_txt = str(r[col_tipo]).upper() if col_tipo and pd.notna(r[col_tipo]) else ""
            tipo_txt_norm = ''.join(c for c in unicodedata.normalize('NFD', tipo_txt) if unicodedata.category(c) != 'Mn')
            
            is_credito = "CREDITO" in tipo_txt_norm or "ENTRADA" in tipo_txt_norm or "C" == tipo_txt_norm
            if not col_tipo or tipo_txt == "":
                is_credito = "-" not in str(r[col_valor])
            
            transacoes.append({
                'Data': dt.strftime('%d/%m/%Y'),
                'dt_obj': dt,
                'Valor': v,
                'Razao_Social': nome_final,
                'Desc_Banco': desc_banco,
                'Is_Credito': is_credito,
                'Cod_Banco_Proprio': cod_banco_identificado,
                'Nome_Arquivo_Origem': file.name
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
                entradas.append({'Fornecedor': fornecedor, 'Valor': val_nota, 'Nota': nota_num, 'Data': dt_nota})
    return entradas

def buscar_codigo_conta(nome_pesquisa, mapa_contas, conta_fallback_receita):
    norm_pesquisa = normalizar_para_match(nome_pesquisa)
    if not norm_pesquisa: return ""
    if any(x in norm_pesquisa for x in ["PIXBET", "FLABET", "BETDASORTE", "SICKBET"]):
        return conta_fallback_receita
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

# --- SIDEBAR PARAMETRIZADA COM OS 3 CÓDIGOS CORRETOS ---
with st.sidebar:
    st.header("⚙️ Parametrização de Bancos e Contas")
    st.info("Informe os códigos reduzidos corretos das 3 contas da empresa:")
    
    txt_celcoin = st.text_input("Código CELCOIN:", value="1868")
    txt_sicoob = st.text_input("Código SICOOB:", value="2093")
    txt_delbank = st.text_input("Código DELBANK / DELFINANCE:", value="1110")
    
    st.divider()
    conta_padrao_receita = st.text_input("Conta de Contraparte Interna (Pix/Aportes):", value="1121")

    configuracao_bancos = {
        "CELCOIN": txt_celcoin.strip(),
        "SICOOB": txt_sicoob.strip(),
        "DELBANK/DELFINANCE": txt_delbank.strip()
    }

st.title("🏦 Portal de Conciliação Multi-Banco Avançado")

tab1, tab2 = st.tabs(["🔄 1. Nova Conciliação (Completa)", "📤 2. Gerar TXT de Planilha Auditada"])

# --- ABA 1 ---
with tab1:
    st.markdown("### Processar Arquivos Brutos")
    colA, colB, colC = st.columns(3)
    with colA: f_extratos = st.file_uploader("📂 Extratos Bancários (Selecione de 1 a 3 extratos livremente)", type=["xlsx","csv","pdf"], accept_multiple_files=True, key="ext1")
    with colB: f_contas = st.file_uploader("🗂️ Arquivo de Contas (Plano de Contas)", type=["xlsx","csv"], key="cont1")
    with colC: f_entradas = st.file_uploader("📥 Relatório de Entradas / Fiscal (Obrigatório)", type=["xlsx","csv"], key="fisc1")

    if f_extratos and f_contas and f_entradas:
        mapa_contas = carregar_cadastro_contas(f_contas)
        cadastro_entradas = carregar_fiscal_entradas(f_entradas)
        
        extrato_lista = []
        for f in f_extratos:
            extrato_lista.extend(ler_extrato_dinamico(f, configuracao_bancos))
            
        # INTERVENÇÃO HUMANA EM TELA
        arquivos_misteriosos = set([tx['Nome_Arquivo_Origem'] for tx in extrato_lista if "PEDIR_AJUDA_DE_" in str(tx['Cod_Banco_Proprio'])])
        
        bancos_resolvidos_na_tela = {}
        if arquivos_misteriosos:
            st.warning("⚠️ Mapeamento Manual Requerido: Não consegui identificar o banco de alguns extratos automaticamente. Selecione a qual conta pertencem:")
            for arq in arquivos_misteriosos:
                escolha = st.selectbox(
                    f"O arquivo contido em '{arq}' refere-se a qual conta configurada?",
                    options=list(configuracao_bancos.keys()),
                    key=f"select_ajuda_{arq}"
                )
                bancos_resolvidos_na_tela[arq] = configuracao_bancos[escolha]
        
        matriz_conciliada = []
        for tx in extrato_lista:
            # Injeta isoladamente o banco correto descoberto para este arquivo específico
            if "PEDIR_AJUDA_DE_" in str(tx['Cod_Banco_Proprio']):
                cod_banco_atual = bancos_resolvidos_na_tela.get(tx['Nome_Arquivo_Origem'], "CONTA_MANUAL")
            else:
                cod_banco_atual = tx['Cod_Banco_Proprio']
                
            codigo_fornecedor = buscar_codigo_conta(tx['Razao_Social'], mapa_contas, conta_padrao_receita)
            
            if tx['Is_Credito']:
                c_deb = cod_banco_atual
                c_crd = codigo_fornecedor if codigo_fornecedor else conta_padrao_receita
                if "TRANSFERENCIA" in tx['Desc_Banco'].upper() or any(x in tx['Razao_Social'].upper() for x in ["PIXBET", "FLABET", "BETDASORTE", "SICKBET"]):
                    hist_final = "RECB TRANSFERENCIA INTERNA ENTRE CONTAS"
                else:
                    hist_final = f"RECB {tx['Razao_Social']}"
            else:
                c_deb = codigo_fornecedor if codigo_fornecedor else "CONTA_MANUAL"
                c_crd = cod_banco_atual
                
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
                'Valor_Original': tx['Valor'], 
                'Valor': formatar_moeda_br(tx['Valor']),
                'Histórico': " ".join(hist_final.upper().split())
            })
            
        df_final = pd.DataFrame(matriz_conciliada)
        
        if not df_final.empty:
            st.success("Conciliação Pré-Processada com Sucesso! Todos os extratos anexados foram individualizados.")
            st.dataframe(df_final[['Data', 'Deb', 'Cred', 'Valor', 'Histórico']], use_container_width=True)
            
            st.markdown("---")
            st.markdown("### 📥 Escolha como deseja exportar:")
            col_btn1, col_btn2 = st.columns(2)
            
            with col_btn1:
                output_excel = io.BytesIO()
                df_excel = df_final[['Data', 'Deb', 'Cred', 'Valor', 'Histórico']].copy()
                with pd.ExcelWriter(output_excel, engine='openpyxl') as writer:
                    df_excel.to_excel(writer, index=False, sheet_name='Conciliacao_Analise')
                st.download_button(
                    label="📥 1. Baixar Planilha para Ajustes (.XLSX)",
                    data=output_excel.getvalue(),
                    file_name=f"Analise_Humana_Conciliacao_{datetime.now().strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
            with col_btn2:
                output_txt = io.StringIO()
                for _, r in df_final.iterrows():
                    val_dominio = formatar_valor_dominio(r['Valor_Original'])
                    output_txt.write(f"{r['Data']};{r['Deb']};{r['Cred']};{val_dominio};;{r['Histórico']};;;;\n")
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
            st.success("Planilha processada!")
            txt_output_audit = io.StringIO()
            
            for _, row in df_audit.iterrows():
                dt_f = str(row[c_dt]).strip().split(' ')[0]
                val_limpo = formatar_valor_dominio(row[c_vl])
                
                deb_f = str(row[c_db]).split('.')[0].strip() if c_db and pd.notna(row[c_db]) else ''
                cred_f = str(row[c_cr]).split('.')[0].strip() if c_cr and pd.notna(row[c_cr]) else ''
                hist_f = str(row[c_hs]).upper().strip()
                
                if deb_f in ["NAN", "CONTA_MANUAL"]: deb_f = ""
                if cred_f in ["NAN", "CONTA_MANUAL"]: cred_f = ""
                
                txt_output_audit.write(f"{dt_f};{deb_f};{cred_f};{val_limpo};;{hist_f};;;;\n")
                
            st.download_button(
                label="📄 Baixar Arquivo Domínio Formatado (.TXT)",
                data=txt_output_audit.getvalue().encode('utf-8'),
                file_name=f"Importacao_Dominio_Editado_{datetime.now().strftime('%Y%m%d')}.txt",
                mime="text/plain",
                use_container_width=True
            )
