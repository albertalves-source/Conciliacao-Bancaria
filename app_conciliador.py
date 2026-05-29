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
    v_str = str(v).upper().replace('R$', '').replace('$', '').replace(' ', '').replace(' ', '').strip()
    v_str = v_str.replace('C', '').replace('D', '').replace('"', '').replace('«', '').replace('»', '').strip()
    
    if not v_str: return 0.0
    
    if '.' in v_str and ',' in v_str:
        v_str = v_str.replace('.', '').replace(',', '.')
    elif ',' in v_str:
        v_str = v_str.replace(',', '.')
        
    try: 
        return abs(float(v_str))
    except: 
        return 0.0

def formatar_valor_dominio(v):
    try:
        val = limpar_valor(v)
        return f"{val:.2f}".replace('.', ',')
    except:
        return "0,00"

def converter_data(data_obj):
    if pd.isna(data_obj): return None
    s = str(data_obj).strip().split(' ')[0].split('T')[0]
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%Y%m%d'):
        try: return datetime.strptime(s, fmt).date()
        except: pass
    try:
        num = float(s)
        if num > 10000: return pd.to_datetime(num, unit='D', origin='1899-12-30').date()
    except: pass
    return None

def higienizar_texto_lista_palavras(texto):
    if not texto: return []
    txt = str(texto).upper().strip()
    txt = ''.join(c for c in unicodedata.normalize('NFD', txt) if unicodedata.category(c) != 'Mn')
    txt = re.sub(r'[^A-Z0-9\s]', '', txt)
    txt = re.sub(r'\s+', ' ', txt).strip()
    
    termos_remover = ["LTDA", "SA", "S/A", "ME", "EIRELI", "SOCIEDADEUNIPESSOAL", "SOLUCOESTECNOLOGICAS", "LTDAME", "DESENVOLVEDORADESISTEMA", "DESENVOLVEDORADESISTEMAS"]
    palavras = txt.split(' ')
    return [p for p in palavras if p and p not in termos_remover]

def normalizar_para_match(texto):
    palavras = higienizar_texto_lista_palavras(texto)
    return "".join(palavras)

# --- MOTOR DETECTOR DE BANCOS ---
def descobrir_codigo_banco_do_extrato(df_bruto, configuracao_bancos, nome_arquivo):
    texto_cabecalho = ""
    for idx, row in df_bruto.head(30).iterrows():
        texto_cabecalho += " " + " ".join([str(x).upper() for x in row.values if pd.notna(x)])
    
    nome_arq_upper = nome_arquivo.upper()
    
    if "SICOOB" in texto_cabecalho or "SICOOB" in nome_arq_upper:
        return configuracao_bancos["SICOOB"]
    elif "DELBANK" in texto_cabecalho or "DELFINANCE" in texto_cabecalho or "DEL FINANCE" in texto_cabecalho or "DELF" in nome_arq_upper or "PIXBET ABRIL DELF" in nome_arq_upper:
        return configuracao_bancos["DELBANK/DELFINANCE"]
    elif "CELCOIN" in texto_cabecalho or "CELCOIN" in nome_arq_upper:
        return configuracao_bancos["CELCOIN"]
            
    return f"PEDIR_AJUDA_DE_{nome_arquivo}"

# --- EXTRATOR DE EXTRATOS BRUTOS ---
def ler_extrato_dinamico(file, configuracao_bancos):
    file.seek(0)
    conteudo_bytes = file.read()
    
    try:
        df = pd.read_excel(io.BytesIO(conteudo_bytes), header=None, dtype=str)
    except:
        try:
            df = pd.read_csv(io.StringIO(conteudo_bytes.decode('utf-8')), header=None, dtype=str, sep=None, engine='python')
        except:
            df = pd.read_csv(io.StringIO(conteudo_bytes.decode('iso-8859-1')), header=None, dtype=str, sep=None, engine='python')
            
    if df.empty: return []
        
    cod_banco_identificado = descobrir_codigo_banco_do_extrato(df, configuracao_bancos, file.name)
    transacoes = []
    
    # FORMATO DELBANK/DELFINANCE
    if cod_banco_identificado == configuracao_bancos["DELBANK/DELFINANCE"]:
        for idx, row in df.iterrows():
            valores_originais = [str(x).strip() for x in row.values if pd.notna(x)]
            if not valores_originais: continue
            
            dt = converter_data(valores_originais[0])
            if not dt: continue
            
            desc_banco = ""
            valor_encontrado = 0.0
            
            for v in valores_originais[1:]:
                v_upper = v.upper()
                if any(x in v_upper for x in ["PIX", "TED", "TRANSFERENCIA", "PAGAMENTO", "BOLETO", "BANCO EMISSOR"]):
                    desc_banco = v
                    break
            
            for v in valores_originais[1:]:
                if any(c in v for c in [',', '.']) and any(c.isdigit() for c in v) and "BANCO" not in v.upper():
                    val_limpo = limpar_valor(v)
                    if val_limpo > 0:
                        valor_encontrado = val_limpo
                        break
            
            if valor_encontrado == 0: continue
            
            linha_completa_txt = " ".join(valores_originais).upper()
            nome_final = ""
            
            if "PARA:" in linha_completa_txt:
                nome_final = linha_completa_txt.split("PARA:")[-1].strip()
            elif "PAGADOR:" in linha_completa_txt:
                nome_final = linha_completa_txt.split("PAGADOR:")[-1].strip()
            
            nome_final = nome_final.split("BANCO EMISSOR:")[0].split("VALOR:")[0].split("R$")[0].strip()
            
            if not nome_final or nome_final == "" or nome_final == "NAN":
                nome_final = desc_banco
                
            if any(x in linha_completa_txt for x in ["TRANSFERENCIA PROPRIETARIA", "PIXBET SOLUCOES"]):
                nome_final = "PIXBET SOLUCOES TECNOLOGICAS LTDA"
                
            is_credito = "PAGADOR" in linha_completa_txt or "RECEBIDA" in linha_completa_txt
            if any(x in linha_completa_txt for x in ["ENVIADO", "ENVIADA", "PAGAMENTO"]):
                is_credito = False
                
            transacoes.append({
                'Data': dt.strftime('%d/%m/%Y'),
                'dt_obj': dt,
                'Valor': valor_encontrado,
                'Razao_Social': nome_final.strip(),
                'Desc_Banco': desc_banco,
                'Is_Credito': is_credito,
                'Cod_Banco_Proprio': cod_banco_identificado,
                'Nome_Arquivo_Origem': file.name
            })
        return transacoes

    # FORMATO CELCOIN E SICOOB
    idx_header = None
    for i, row in df.iterrows():
        valores = [str(x).strip().upper() for x in row.values if pd.notna(x)]
        if any(term in valores for term in ["NOME CONTRAPARTE", "DESCRIÇÃO", "HISTÓRICO", "DESCRICAO", "FAVORECIDO", "VALOR", "DEB/CRED"]):
            idx_header = i
            break
            
    if idx_header is not None:
        headers = [str(c).strip().upper() for c in df.iloc[idx_header].values]
        dados = df.iloc[idx_header+1:].copy()
        dados.columns = headers
        
        col_data = next((c for c in headers if "DATA" in c or "DT" in c), None)
        col_tipo = next((c for c in headers if "TIPO" in c or "NATUREZA" in c or "DEB/CRED" in c), None)
        col_desc_banco = next((c for c in headers if "DESCRI" in c or "HIST" in c), None)
        col_contraparte = next((c for c in headers if "CONTRAPARTE" in c or "FAVORECIDO" in c or "NOME" in c), None)
        col_valor = next((c for c in headers if "VALOR" in c or "QUANTIA" in c or "VALOR (R$)" in c), None)
        
        for _, r in dados.iterrows():
            if pd.isna(r.get(col_data)) or pd.isna(r.get(col_valor)): continue
            
            dt = converter_data(r[col_data])
            v = limpar_valor(r[col_valor])
            if v == 0 or not dt: continue
            
            desc_banco = str(r[col_desc_banco]).strip() if col_desc_banco and pd.notna(r[col_desc_banco]) else ""
            
            nome_final = ""
            if col_contraparte and col_contraparte in r and pd.notna(r[col_contraparte]):
                nome_final = str(r[col_contraparte]).strip()
            
            if not nome_final or nome_final == "" or nome_final.upper() == "NAN":
                nome_final = desc_banco
                
            nome_final_upper = nome_final.upper()
            if "PARA:" in nome_final_upper:
                nome_final = nome_final.split("PARA:")[-1].strip()
            elif "PAGADOR:" in nome_final_upper:
                nome_final = nome_final.split("PAGADOR:")[-1].strip()
            elif "TRANSFERENCIA PROPRIETARIA" in nome_final_upper or "SALDO DO DIA" in nome_final_upper:
                nome_final = "PIXBET SOLUCOES TECNOLOGICAS LTDA"
                
            texto_linha_completo = (str(r[col_valor]) + " " + (str(r[col_tipo]) if col_tipo else "")).upper()
            is_credito = "CREDITO" in texto_linha_completo or "ENTRADA" in texto_linha_completo or " C" in texto_linha_completo or texto_linha_completo.endswith("C")
            if "DEBITO" in texto_linha_completo or "SAIDA" in texto_linha_completo or " D" in texto_linha_completo or texto_linha_completo.endswith("D") or "-" in str(r[col_valor]):
                is_credito = False
                
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

# --- CARREGADORES DO FISCAL E PLANO DE CONTAS ---
def carregar_cadastro_contas(file):
    file.seek(0)
    conteudo = file.read()
    try: df = pd.read_excel(io.BytesIO(conteudo), header=None, dtype=str)
    except: df = pd.read_csv(io.StringIO(conteudo.decode('utf-8')), header=None, dtype=str, sep=None, engine='python')
    
    mapa = {}
    for _, r in df.iterrows():
        valores = [str(x).strip() for x in r.values if pd.notna(x)]
        if len(valores) >= 2:
            cod = valores[0].split('.')[0]
            nome_completo_cadastrado = valores[-1].upper().strip()
            if cod.isdigit():
                palavras_chave = higienizar_texto_lista_palavras(nome_completo_cadastrado)
                if palavras_chave:
                    mapa[cod] = {
                        'palavras': palavras_chave,
                        'nome_completo': nome_completo_cadastrado
                    }
    return mapa

def carregar_fiscal_entradas(file):
    file.seek(0)
    conteudo = file.read()
    try: df = pd.read_excel(io.BytesIO(conteudo), header=None, dtype=str)
    except: df = pd.read_csv(io.StringIO(conteudo.decode('utf-8')), header=None, dtype=str, sep=None, engine='python')
        
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

# --- BUSCA CONTÁBIL RIGOROSA CORRIGIDA ---
def buscar_dados_conta_completos(nome_pesquisa, mapa_contas, conta_fallback_receita):
    norm_pesquisa = normalizar_para_match(nome_pesquisa)
    if not norm_pesquisa: return "", nome_pesquisa.upper().strip()
    
    if any(x in norm_pesquisa for x in ["PIXBET", "FLABET", "BETDASORTE", "SICKBET"]):
        return conta_fallback_receita, "PIXBET SOLUCOES TECNOLOGICAS LTDA"
        
    regras_bancarias_fixas = {
        "DEBCONVTRIBUTOSFEDERAISRFB": ("2541", "DEB CONV TRIBUTOS FEDERAIS RFB"), 
        "DEBTITCOMPEEFETIVADO": ("2100", "DEB TIT COMPE EFETIVADO"),       
        "DEBTITULOCOBRANCA": ("2100", "DEB TITULO COBRANCA"),          
        "DEBITOPACOTESERVICOS": ("4122", "DEBITO PACOTE SERVICOS"),
        "PAGAMENTODEBOLETO": ("2100", "PAGAMENTO DE BOLETO")
    }
    
    # Resolvido o erro de sintaxe da atribuição walrus :=
    if norm_pesquisa in regras_bancarias_fixas:
        return regras_bancarias_fixas[norm_pesquisa]
        
    palavras_extrato = higienizar_texto_lista_palavras(nome_pesquisa)
    
    # 1. Cruzamento Exato Líquido (Garante correspondências perfeitas)
    for cod_reduzido, dados in mapa_contas.items():
        if "".join(palavras_extrato) == "".join(dados['palavras']):
            return cod_reduzido, dados['nome_completo']
            
    # 2. SEGUNDA TRAVA SEVERA: Casamento rigoroso de primeiro nome + sobrenomes extensos (Mínimo 2 termos completos)
    if len(palavras_extrato) >= 2:
        for cod_reduzido, dados in mapa_contas.items():
            palavras_cad = dados['palavras']
            tamanho_corte = min(len(palavras_extrato), len(palavras_cad))
            if palavras_extrato[:tamanho_corte] == palavras_cad[:tamanho_corte]:
                return cod_reduzido, dados['nome_completo']
                
    # Se não houver certeza absoluta e exatidão, o sistema não chuta! Mantém o nome do extrato original intacto
    return "", nome_pesquisa.upper().strip()

# --- SIDEBAR PARAMETRIZADA ---
with st.sidebar:
    st.header("⚙️ Parametrização de Bancos e Contas")
    st.info("Informe os códigos reduzidos corretos das 3 contas da empresa:")
    
    txt_celcoin = st.text_input("Código CELCOIN:", value="1868")
    txt_sicoob = st.text_input("Código SICOOB:", value="2093")
    txt_delbank = st.text_input("Código DELBANK / DELFINANCE:", value="1110")
    
    st.divider()
    conta_padrao_receita = st.text_input("Conta Interna Operacional (Bancas/Pix):", value="1121")

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
    with colA: f_extratos = st.file_uploader("📂 Extratos Bancários", type=["xlsx","csv","pdf"], accept_multiple_files=True, key="ext1")
    with colB: f_contas = st.file_uploader("🗂️ Arquivo de Contas (Plano de Contas)", type=["xlsx","csv"], key="cont1")
    with colC: f_entradas = st.file_uploader("📥 Relatório de Entradas / Fiscal (Obrigatório)", type=["xlsx","csv"], key="fisc1")

    if f_extratos and f_contas and f_entradas:
        mapa_contas = carregar_cadastro_contas(f_contas)
        cadastro_entradas = carregar_fiscal_entradas(f_entradas)
        
        extrato_lista = []
        for f in f_extratos:
            extrato_lista.extend(ler_extrato_dinamico(f, configuracao_bancos))
            
        matriz_conciliada = []
        for tx in extrato_lista:
            cod_banco_atual = tx['Cod_Banco_Proprio']
            
            # Puxa os dados contábeis validados do Plano de Contas
            codigo_fornecedor, nome_final_extenso = buscar_dados_conta_completos(tx['Razao_Social'], mapa_contas, conta_padrao_receita)
            
            if tx['Is_Credito']:
                c_deb = cod_banco_atual
                c_crd = codigo_fornecedor if codigo_fornecedor else conta_padrao_receita
                if "TRANSFERENCIA" in tx['Desc_Banco'].upper() or any(x in normalizar_para_match(tx['Razao_Social']) for x in ["PIXBET", "FLABET", "BETDASORTE", "SICKBET"]):
                    hist_final = "RECB TRANSFERENCIA INTERNA ENTRE CONTAS"
                else:
                    hist_final = f"RECEB {nome_final_extenso}"
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
                    hist_final = f"PAGTO NF {nota_vinculada} {nome_final_extenso}"
                else:
                    hist_final = f"PAGTO {nome_final_extenso}"
                    
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
            st.success("Conciliação Processada com Sucesso! Os históricos de nomes foram corrigidos.")
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
                    file_name=f"Conciliacao_{datetime.now().strftime('%Y%m%d')}.xlsx",
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
