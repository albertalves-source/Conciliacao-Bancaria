import streamlit as st
import pandas as pd
import re
import io
import warnings
import unicodedata
from datetime import datetime

st.set_page_config(page_title="Portal de Conciliação Individual", layout="wide", page_icon="🏦")
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

# --- DETECTOR UNIVERSAL DE BANCOS ---
def extrair_nome_banco_por_extenso(df_bruto, nome_arquivo):
    texto_cabecalho = ""
    for idx, row in df_bruto.head(15).iterrows():
        texto_cabecalho += " " + " ".join([str(x).upper() for x in row.values if pd.notna(x)])
    
    nome_arq_upper = nome_arquivo.upper()
    
    if "SICOOB" in texto_cabecalho or "SICOOB" in nome_arq_upper:
        return "SICOOB"
    elif "DELBANK" in texto_cabecalho or "DELFINANCE" in texto_cabecalho or "DEL FINANCE" in texto_cabecalho or "DELF" in nome_arq_upper:
        return "DELFINANCE"
    elif "CELCOIN" in texto_cabecalho or "CELCOIN" in nome_arq_upper:
        return "CELCOIN"
    return "BANCO_INDETERMINADO"

# --- EXTRATOR DE EXTRATOS INTELIGENTE COM LOOK-AHEAD PARA LAYOUT MULTI-LINHAS SICOOB ---
def ler_extrato_dinamico(file):
    file.seek(0)
    conteudo_bytes = file.read()
    
    try: df = pd.read_excel(io.BytesIO(conteudo_bytes), header=None, dtype=str)
    except:
        try: df = pd.read_csv(io.StringIO(conteudo_bytes.decode('utf-8')), header=None, dtype=str, sep=None, engine='python')
        except: df = pd.read_csv(io.StringIO(conteudo_bytes.decode('iso-8859-1')), header=None, dtype=str, sep=None, engine='python')
            
    if df.empty: return [], "BANCO_INDETERMINADO"
        
    nome_banco_detectado = extrair_nome_banco_por_extenso(df, file.name)
    transacoes = []
    
    # --- FLUXO 1: DELBANK / DELFINANCE ---
    if nome_banco_detectado == "DELFINANCE":
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
            if not nome_final or nome_final == "" or nome_final == "NAN": nome_final = desc_banco
            if any(x in linha_completa_txt for x in ["TRANSFERENCIA PROPRIETARIA", "PIXBET SOLUCOES"]):
                nome_final = "PIXBET SOLUCOES TECNOLOGICAS LTDA"
                
            is_credito = "PAGADOR" in linha_completa_txt or "RECEBIDA" in linha_completa_txt
            if any(x in linha_completa_txt for x in ["ENVIADO", "ENVIADA", "PAGAMENTO"]): is_credito = False
                
            transacoes.append({
                'Data': dt.strftime('%d/%m/%Y'), 'Valor': valor_encontrado, 'Razao_Social': nome_final.strip(),
                'Desc_Banco': desc_banco, 'Is_Credito': is_credito
            })
        return transacoes, nome_banco_detectado

    # --- FLUXO 2: CELCOIN E SICOOB (COMPATIBILIDADE MULTI-LINHAS) ---
    idx_header = None
    for i, row in df.iterrows():
        valores = [str(x).strip().upper() for x in row.values if pd.notna(x)]
        if any(term in valores for term in ["NOME CONTRAPARTE", "DESCRIÇÃO", "HISTÓRICO", "DESCRICAO", "FAVORECIDO", "VALOR", "DEB/CRED"]):
            idx_header = i
            break
            
    if idx_header is not None:
        headers = [str(c).strip().upper() for c in df.iloc[idx_header].values]
        dados_lista = list(df.iloc[idx_header+1:].values)
        num_colunas = df.shape[1]
        
        # Mapeamento de índices de colunas com base no cabeçalho detectado
        pos_data = next((idx for idx, c in enumerate(headers) if "DATA" in str(c) or "DT" in str(c)), 0)
        pos_hist = next((idx for idx, c in enumerate(headers) if "HIST" in str(c) or "DESC" in str(c) or "CONTRA" in str(c) or "FAVOR" in str(c)), 2)
        pos_valor = next((idx for idx, c in enumerate(headers) if "VALOR" in str(c) or "QUANT" in str(c)), num_colunas - 1)
        
        total_linhas = len(dados_lista)
        idx_cursor = 0
        
        while idx_cursor < total_linhas:
            linha_atual = dados_lista[idx_cursor]
            
            val_data_celula = linha_atual[pos_data]
            dt = converter_data(val_data_celula)
            
            # Se a linha não tiver uma data inicial contábil válida, pula para a próxima
            if not dt:
                idx_cursor += 1
                continue
                
            val_original_banco = linha_atual[pos_valor]
            v = limpar_valor(val_original_banco)
            if v == 0:
                idx_cursor += 1
                continue
                
            historico_principal = str(linha_atual[pos_hist]).strip()
            
            # --- MOTOR LOOK-AHEAD: Varre até as 4 linhas abaixo procurando dados anexados do Pix ---
            texto_linhas_anexas = []
            idx_sub = idx_cursor + 1
            while idx_sub < total_linhas:
                linha_sub = dados_lista[idx_sub]
                # Se a próxima linha já tiver uma nova data ou um novo valor, ela pertence a outro lançamento! Para a busca.
                if converter_data(linha_sub[pos_data]) or limpar_valor(linha_sub[pos_valor]) > 0:
                    break
                    
                conteudo_linha_sub = " ".join([str(x).strip() for x in linha_sub if pd.notna(x)])
                if conteudo_linha_sub:
                    texto_linhas_anexas.append(conteudo_linha_sub)
                idx_sub += 1
                
            # Junta tudo para extrair o favorecido definitivo do Sicoob
            bloco_texto_completo = (historico_principal + " " + " ".join(texto_linhas_anexas)).upper()
            
            nome_final = historico_principal
            
            # Captura cirúrgica de nomes limpos ignorando metadados como CPF/CNPJ de linhas secundárias
            if "RECEBIMENTO PIX" in bloco_texto_completo or "PAGAMENTO PIX" in bloco_texto_completo:
                for texto_linha in texto_linhas_anexas:
                    txt_u = texto_linha.upper()
                    if not any(k in txt_u for x in txt_u for k in ["PAGAMENTO PIX", "RECEBIMENTO PIX", "SOLICITACAO PIX"]) and not re.search(r'^\d', txt_u) and not re.search(r'^\*', txt_u):
                        if len(txt_u) > 3:
                            nome_final = texto_linha
                            break
                            
            nome_final_upper = nome_final.upper()
            if "PARA:" in nome_final_upper: nome_final = nome_final.split("PARA:")[-1].strip()
            elif "PAGADOR:" in nome_final_upper: nome_final = nome_final.split("PAGADOR:")[-1].strip()
            elif "TRANSFERENCIA PROPRIETARIA" in nome_final_upper or "SALDO DO DIA" in nome_final_upper:
                nome_final = "PIXBET SOLUCOES TECNOLOGICAS LTDA"
                
            is_credito = "CREDITO" in bloco_texto_completo or "ENTRADA" in bloco_texto_completo or "RECEB" in bloco_texto_completo or str(val_original_banco).endswith("C")
            if "DEBITO" in bloco_texto_completo or "SAIDA" in bloco_texto_completo or "EMITIDO" in bloco_texto_completo or str(val_original_banco).endswith("D") or "-" in str(val_original_banco):
                is_credito = False
                
            if "SALDO DO DIA" in bloco_texto_completo:
                idx_cursor = idx_sub
                continue
                
            transacoes.append({
                'Data': dt.strftime('%d/%m/%Y'), 'Valor': v, 'Razao_Social': nome_final.strip(),
                'Desc_Banco': historico_principal, 'Is_Credito': is_credito
            })
            idx_cursor = idx_sub
            
    return transacoes, nome_banco_detectado

# --- CARREGADORES CONTÁBEIS ---
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
                    mapa[cod] = {'palavras': palavras_chave, 'nome_completo': nome_completo_cadastrado}
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
        if "TOTAL ACUMULADOR" in linha_str or "TOTAL GERAL" in linha_str or "ACOMPANHAMENTO" in linha_str: continue
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
            nota_num = valores[2] if len(valores) > 2 and valores[2].isdigit() else (re.search(r'\b\d{1,13}\b', linha_str).group(0) if re.search(r'\b\d{1,13}\b', linha_str) else "")
            if fornecedor: entradas.append({'Fornecedor': fornecedor, 'Valor': val_nota, 'Nota': nota_num, 'Data': dt_nota})
    return entradas

# --- BUSCA RIGOROSA CONTÁBIL EXCLUSIVA (MATCH 100% EXATO POR EXTEBNSO - CONTRA OUTRAS MARIAS) ---
def buscar_dados_conta_completos(nome_pesquisa, mapa_contas, conta_fallback_receita):
    norm_pesquisa = normalizar_para_match(nome_pesquisa)
    if not norm_pesquisa: return "", nome_pesquisa.upper().strip()
    if any(x in norm_pesquisa for x in ["PIXBET", "FLABET", "BETDASORTE", "SICKBET"]): return conta_fallback_receita, "PIXBET SOLUCOES TECNOLOGICAS LTDA"
        
    palavras_extrato = higienizar_texto_lista_palavras(nome_pesquisa)
    
    # 1. Trava Absoluta: O nome deve ser 100% idêntico ao plano contábil importado
    for cod, dados in mapa_contas.items():
        if "".join(palavras_extrato) == "".join(dados['palavras']): return cod, dados['nome_completo']
            
    # 2. Match Seguro de Prefixo de Sobrenome Completo (Exige mínimo de 3 palavras exatas batendo para não misturar as Marias)
    if len(palavras_extrato) >= 3:
        for cod, dados in mapa_contas.items():
            palavras_cad = dados['palavras']
            tamanho_corte = min(len(palavras_extrato), len(palavras_cad))
            if tamanho_corte >= 3 and list(palavras_extrato[:tamanho_corte]) == list(palavras_cad[:tamanho_corte]):
                return cod, dados['nome_completo']
                
    return "", nome_pesquisa.upper().strip()

# --- SIDEBAR UNIVERSAL CONTÁBIL ---
with st.sidebar:
    st.header("⚙️ Código Reduzido do Banco")
    st.info("Informe qual conta reduzida da empresa refere-se ao extrato que você anexou:")
    txt_codigo_banco_universal = st.text_input("Código do Banco Atual:", value="2093")
    st.divider()
    conta_padrao_receita = st.text_input("Conta Interna Operacional (Bancas/Pix):", value="1121")

st.title("🏦 Portal de Conciliação Avançado (Modo Individualizado)")

tab1, tab2 = st.tabs(["🔄 1. Conciliar Um Banco", "📤 2. Gerar TXT de Planilha Auditada"])

with tab1:
    st.markdown("### Processar Arquivo Atual")
    colA, colB, colC = st.columns(3)
    with colA: f_extrato = st.file_uploader("📂 Anexe UM Extrato Bancário", type=["xlsx","csv","pdf"], accept_multiple_files=False, key="ext1")
    with colB: f_contas = st.file_uploader("🗂️ Arquivo de Contas (Plano de Contas)", type=["xlsx","csv"], key="cont1")
    with colC: f_entradas = st.file_uploader("📥 Relatório de Entradas / Fiscal (Obrigatório)", type=["xlsx","csv"], key="fisc1")

    if f_extrato and f_contas and f_entradas:
        mapa_contas = carregar_cadastro_contas(f_contas)
        cadastro_entradas = carregar_fiscal_entradas(f_entradas)
        
        extrato_lista, nome_banco_detectado = ler_extrato_dinamico(f_extrato)
        cod_banco_atual = txt_codigo_banco_universal.strip()
        
        matriz_conciliada = []
        for tx in extrato_lista:
            codigo_fornecedor, nome_final_extenso = buscar_dados_conta_completos(tx['Razao_Social'], mapa_contas, conta_padrao_receita)
            
            if tx['Is_Credito']:
                c_deb = cod_banco_atual
                c_crd = codigo_fornecedor if codigo_fornecedor else conta_padrao_receita
                hist_final = "RECB TRANSFERENCIA INTERNA ENTRE CONTAS" if any(x in normalizar_para_match(tx['Razao_Social']) for x in ["PIXBET", "FLABET", "BETDASORTE", "SICKBET"]) else f"RECEB {nome_final_extenso}"
            else:
                c_deb = codigo_fornecedor if codigo_fornecedor else "CONTA_MANUAL"
                c_crd = cod_banco_atual
                
                nota_vinculada = ""
                norm_tx_nome = normalizar_para_match(tx['Razao_Social'])
                for ent in cadastro_entradas:
                    if norm_tx_nome and (norm_tx_nome in normalizar_para_match(ent['Fornecedor']) or normalizar_para_match(ent['Fornecedor']) in norm_tx_nome):
                        nota_vinculada = ent['Nota']
                        break
                hist_final = f"PAGTO NF {nota_vinculada} {nome_final_extenso}" if nota_vinculada else f"PAGTO {nome_final_extenso}"
                    
            matriz_conciliada.append({
                'Data': tx['Data'], 'Deb': c_deb, 'Cred': c_crd, 'Valor_Original': tx['Valor'], 
                'Valor': formatar_moeda_br(tx['Valor']), 'Histórico': " ".join(hist_final.upper().split())
            })
            
        df_final = pd.DataFrame(matriz_conciliada)
        
        if not df_final.empty:
            st.success(f"Conciliação do banco {nome_banco_detectado} Processada com Sucesso!")
            st.dataframe(df_final[['Data', 'Deb', 'Cred', 'Valor', 'Histórico']], use_container_width=True)
            
            st.markdown("---")
            st.markdown("### 📥 Baixar Lançamentos:")
            col_btn1, col_btn2 = st.columns(2)
            
            data_atual_str = datetime.now().strftime('%Y%m%d')
            
            with col_btn1:
                output_excel = io.BytesIO()
                df_excel = df_final[['Data', 'Deb', 'Cred', 'Valor', 'Histórico']].copy()
                with pd.ExcelWriter(output_excel, engine='openpyxl') as writer:
                    df_excel.to_excel(writer, index=False, sheet_name='Conciliacao_Analise')
                st.download_button(
                    label=f"📥 Baixar Planilha Conciliação {nome_banco_detectado} (.XLSX)",
                    data=output_excel.getvalue(),
                    file_name=f"conciliacao_{nome_banco_detectado}_{data_atual_str}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
            with col_btn2:
                output_txt = io.StringIO()
                for _, r in df_final.iterrows():
                    val_dominio = formatar_valor_dominio(r['Valor_Original'])
                    output_txt.write(f"{r['Data']};{r['Deb']};{r['Cred']};{val_dominio};;{r['Histórico']};;;;\n")
                st.download_button(
                    label=f"📄 Gerar TXT Domínio {nome_banco_detectado} (.TXT)",
                    data=output_txt.getvalue().encode('utf-8'),
                    file_name=f"conciliacao_{nome_banco_detectado}_{data_atual_str}.txt",
                    mime="text/plain",
                    use_container_width=True
                )

with tab2:
    st.markdown("### Gerar TXT de Planilha Prontamente Editada / Auditada")
    f_editado = st.file_uploader("📥 Anexe a planilha auditada (.xlsx)", type=["xlsx"], key="edit2")
    if f_editado:
        df_audit = pd.read_excel(f_editado, dtype=str)
        cols = {str(c).upper().strip(): c for c in df_audit.columns}
        c_dt, c_db, c_cr, c_vl, c_hs = cols.get('DATA'), cols.get('DEB'), cols.get('CRED'), cols.get('VALOR'), cols.get('HISTÓRICO')
        
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
