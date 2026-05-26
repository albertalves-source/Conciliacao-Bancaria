import streamlit as st
import pandas as pd
import re
import io
import csv
import warnings
import unicodedata
from datetime import datetime

st.set_page_config(page_title="Portal de Conciliação - Padrão Domínio", layout="wide", page_icon="🏦")
warnings.filterwarnings("ignore")

# ==========================================
# 🧠 DICIONÁRIO DE REGRAS FIXAS (FORÇA BRUTA)
# ==========================================
REGRAS_FIXAS_FORNECEDOR = {
    "PIXBET": {"cod": "5", "nome": "PIXBET SOLUCOES TECNOLOGICAS LTDA"},
    "LEGITIMUZ": {"cod": "1352", "nome": "LEGITIMUZ TECNOLOGIA LTDA"},
    "LUCK VIAGENS": {"cod": "1668", "nome": "AGENCIA LUCK VIAGENS E TURISMO LTDA"},
    "ESMERA": {"cod": "1703", "nome": "ESMERA EMPREENDIMENTOS IMOBILIARIOS LTDA"},
    "CELCOIN": {"cod": "5", "nome": "PIXBET SOLUCOES TECNOLOGICAS LTDA"}, 
    "CONNECTPS": {"cod": "5", "nome": "PIXBET SOLUCOES TECNOLOGICAS LTDA"},
    "DELBANK": {"cod": "5", "nome": "PIXBET SOLUCOES TECNOLOGICAS LTDA"}
}

# --- FUNÇÕES DE APOIO ---
def formatar_moeda_br(v):
    try:
        val = float(v)
        return f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except: return "0,00"

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
    s = str(data_obj).strip().split(' ')[0]
    
    if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        try: return datetime.strptime(s, '%Y-%m-%d').date()
        except: pass
        
    if re.match(r'^\d{2}/\d{2}/\d{4}$', s):
        try: return datetime.strptime(s, '%d/%m/%Y').date()
        except: pass
        
    try: return pd.to_datetime(s, dayfirst=True).date()
    except: return None

def normalizar_espacos(texto):
    if not isinstance(texto, str): return ""
    return " ".join(texto.upper().split())

def normalizar_para_match(texto):
    if not texto: return ""
    txt = str(texto).upper().strip()
    txt = ''.join(c for c in unicodedata.normalize('NFD', txt) if unicodedata.category(c) != 'Mn')
    txt = re.sub(r'[\s\-,.\/]', '', txt)
    for termo in ["LTDA", "SA", "S/A", "ME", "EIRELI", "FILHO", "PARTICIPACOES", "SERVICOS", "COMERCIO", "MARKETING", "DIGITAL", "TECNOLOGIA"]:
        txt = txt.replace(termo, "")
    return txt

def sanitize_dataframe_for_excel(df):
    illegal_chars = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]')
    for col in df.select_dtypes(include=['object']):
        df[col] = df[col].apply(lambda x: illegal_chars.sub('', str(x)) if pd.notna(x) else x)
    return df

# 🎯 EXTRATOR CIRÚRGICO DE NOMES E NOTAS FISCAIS
def extrair_nome_nota_banco(texto):
    t = str(texto).upper().strip()
    
    if any(x in t for x in ["TRANSFERENCIA PROPRIETARIA", "TRANFERENCIA PROPRIETARIA"]):
        return "TRANSFERENCIA PROPRIETARIA", ""
    if "TRANSFERENCIA TRANSACIONAL" in t:
        return "TRANSFERENCIA TRANSACIONAL", ""
    if "TRANSFERENCIA INTERNA" in t or "SALDO INICIAL" in t:
        return "TRANSFERENCIA INTERNA ENTRE CONTAS", ""
    if "BOLETO" in t:
        return "PAGAMENTO DE BOLETO", ""
    if "TAXAS E TARIFAS" in t:
        return "TAXAS E TARIFAS BANCARIAS", ""
        
    nota_match = re.search(r'\bNF\s*0*(\d+)\b', t)
    nota = nota_match.group(1) if nota_match else ""
    
    t = re.sub(r'\bE\d{14}[A-Z0-9]*\b', '', t)
    
    prefixos = r'^(?:PAGTO|RECB|PG\.|D[EÉ]BITO TRANSFERE|D[EÉ]BITO TRANFEREN|PIX ENVIADO|CR[EÉ]DITO PIX RECEBIDO|PIX RECEBIDO|CR[EÉ]DITO TRANSFERE|CR[EÉ]DITO DEVOLU[CÇ][AÃ]O|DEVOLU[CÇ][AÃ]O PIX RECEBIDA|DESCONTO DE|D[EÉ]BITO|CR[EÉ]DITO)\s*'
    sufixos = r'\s*(?:CELCOIN IP|CELCOIN|BANCO ISPB|ISPB|DELBANK|C6 BANK|DOCK IP S\.A\.?|DOCK IP|BCO DO|BCO\b|S\.A\.|LTDA\.|S\.A|LTDA\b).*$'
    
    t = re.sub(prefixos, '', t).strip()
    t = re.sub(sufixos, '', t).strip()
    t = re.sub(r'\bNF\s*\d+\b', '', t)
    t = re.sub(r'\b[A-F0-9]{4,10}\b', '', t) 
    
    t = t.replace('-', ' ')
    nome_limpo = normalizar_espacos(t).strip(' ,"-.')
    return nome_limpo, nota

def buscar_codigo_fornecedor(nome_pesquisa, dicionario_fornecedores):
    if not nome_pesquisa: return "", ""
    nome_limpo = str(nome_pesquisa).upper()
    
    for palavra_chave, dados in REGRAS_FIXAS_FORNECEDOR.items():
        if palavra_chave in nome_limpo:
            return dados["cod"], dados["nome"]

    nome_pesquisa_norm = normalizar_para_match(nome_limpo)
    if not nome_pesquisa_norm: return "", ""
    
    if nome_pesquisa_norm in dicionario_fornecedores: 
        return dicionario_fornecedores[nome_pesquisa_norm]["cod"], dicionario_fornecedores[nome_pesquisa_norm]["nome"]
        
    for nome_bd_norm, dados in dicionario_fornecedores.items():
        if nome_pesquisa_norm.startswith(nome_bd_norm) or nome_bd_norm.startswith(nome_pesquisa_norm):
            return dados["cod"], dados["nome"]

    if len(nome_pesquisa_norm) >= 6:
        for nome_bd_norm, dados in dicionario_fornecedores.items():
            if (nome_pesquisa_norm in nome_bd_norm) or (nome_bd_norm in nome_pesquisa_norm):
                return dados["cod"], dados["nome"]
                
    return "", ""

def carregar_fiscal_seguro(arquivo):
    arquivo.seek(0)
    if arquivo.name.lower().endswith('.csv'):
        try: df_temp = pd.read_csv(arquivo, header=None, dtype=str, sep=None, engine='python')
        except: df_temp = pd.read_csv(arquivo, header=None, dtype=str)
    else:
        df_temp = pd.read_excel(arquivo, header=None, dtype=str)
        
    idx_header = 0
    for i, row in df_temp.iterrows():
        valores = [str(x).strip().upper() for x in row.values if pd.notna(x)]
        if "FORNECEDOR" in valores or "VALOR CONTÁBIL" in valores or "VALOR CONTABIL" in valores:
            idx_header = i
            break
            
    df = df_temp.iloc[idx_header+1:].copy()
    colunas_brutas = [str(c).strip().upper() for c in df_temp.iloc[idx_header].values]
    colunas_limpas = []
    codigo_count = 0
    total_codigos = len([x for x in colunas_brutas if x in ['CÓDIGO', 'CODIGO', 'COD']])
    
    for i, c in enumerate(colunas_brutas):
        c_str = str(c).strip().upper()
        if c_str in ['NAN', 'NONE', '']: colunas_limpas.append(f"COL_{i}")
        elif c_str in ['CÓDIGO', 'CODIGO', 'COD']:
            codigo_count += 1
            if codigo_count == 2 or total_codigos == 1: colunas_limpas.append('codigo_fornecedor_doc')
            else: colunas_limpas.append('codigo_lancamento')
        elif c_str in ['AC.', 'ACUMULADOR']: colunas_limpas.append('acumulador')
        elif c_str in ['NOTA', 'DOC']: colunas_limpas.append('doc')
        elif c_str in ['DATA']: colunas_limpas.append('data')
        elif 'VALOR CONT' in c_str or c_str == 'VALOR_TOTAL': colunas_limpas.append('valor_total')
        elif c_str == 'FORNECEDOR': colunas_limpas.append('nome_fornecedor')
        elif c_str in ['CFOP']: colunas_limpas.append('cfop')
        elif c_str in ['TIPO']: colunas_limpas.append('valor_imposto')
        else: colunas_limpas.append(c_str)
            
    df.columns = colunas_limpas
    return df

def extrair_dados_extrato(file, termos_ignorar):
    transacoes = []
    nome_arquivo = file.name.lower()
    
    if nome_arquivo.endswith(".pdf"):
        try:
            import pdfplumber
            with pdfplumber.open(file) as pdf:
                for page in pdf.pages:
                    texto_bruto = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
                    texto_bruto = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', texto_bruto)
                    
                    for linha in texto_bruto.split('\n'):
                        linha_upper = linha.strip().upper()
                        if not linha_upper: continue
                        if any(x in linha_upper for x in ["SALDO INICIAL", "SALDO FINAL", "TOTAL ACUMULADOR"]): continue
                        
                        # 🔪 O SEPARADOR DE ALTA PRECISÃO (Fatia Data + Código Conta 2139 + Valor + Texto)
                        match_grudado = re.search(r'(\d{2}/\d{2}/\d{4})\s*(2139|\d{4})\s*(-?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2})\s*(.*)', linha_upper)
                        
                        if match_grudado:
                            str_data = match_grudado.group(1)
                            str_valor = match_grudado.group(3)
                            desc_bruta = match_grudado.group(4).strip()
                            
                            is_debito = "-" in str_valor or "PAGTO" in desc_bruta or "DÉBITO" in desc_bruta or "DEBITO" in desc_bruta
                            is_credito = any(x in desc_bruta for x in ["RECEBID", "DEVOLU", "ESTORNO", "CREDIT", "RECB"]) if not is_debito else False
                            if "TRANSFERENCIA" in desc_bruta and not is_debito:
                                is_credito = True
                                
                            val = abs(limpar_valor(str_valor))
                            if val > 0:
                                transacoes.append({'Data': str_data, 'Total': val, 'Fav': desc_bruta, 'Is_Credito': is_credito})
                        else:
                            # Plano B para linhas normais não grudadas
                            data_match = re.search(r'(\d{2}/\d{2}/\d{4})', linha_upper)
                            valor_match = re.findall(r'-?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}', linha_upper)
                            if data_match and valor_match:
                                str_data = data_match.group(1)
                                str_valor = valor_match[-1]
                                val = abs(limpar_valor(str_valor))
                                desc_bruta = linha_upper.replace(str_data, "").replace(str_valor, "").strip()
                                
                                is_debito = "-" in str_valor or "PAGTO" in desc_bruta or "DEBITO" in desc_bruta
                                is_credito = any(x in desc_bruta for x in ["RECEBID", "DEVOLU", "ESTORNO", "CREDIT", "RECB"])
                                if "TRANSFERENCIA" in desc_bruta and not is_debito:
                                    is_credito = True
                                    
                                if val > 0:
                                    transacoes.append({'Data': str_data, 'Total': val, 'Fav': desc_bruta, 'Is_Credito': is_credito})
        except Exception as e: st.error(f"Erro ao ler PDF: {e}")
        
    elif nome_arquivo.endswith(".xlsx") or nome_arquivo.endswith(".xls") or nome_arquivo.endswith(".csv"):
        try:
            if nome_arquivo.endswith(".csv"):
                try: df_ext = pd.read_csv(file, sep=None, engine='python', dtype=str)
                except: df_ext = pd.read_csv(file, dtype=str)
            else:
                df_ext = pd.read_excel(file, dtype=str)
                
            idx_header = 0
            for i, row in df_ext.iterrows():
                row_str = [str(x).upper() for x in row.values if pd.notna(x)]
                if any("DATA" in x or "VALOR" in x or "HISTORICO" in x for x in row_str):
                    idx_header = i
                    df_ext.columns = [str(c).strip().upper() for c in row.values]
                    df_ext = df_ext.iloc[i+1:].copy()
                    break
                    
            col_data, col_desc, col_valor = None, None, None
            for col in df_ext.columns:
                c_norm = normalizar_para_match(col)
                if "DATA" in c_norm: col_data = col
                elif any(x in c_norm for x in ["HISTORICO", "DESCRICAO", "DETALHE"]): col_desc = col
                elif any(x in c_norm for x in ["VALOR", "SAIDA", "ENTRADA"]): col_valor = col
                
            if not col_data and len(df_ext.columns) >= 3:
                col_data, col_desc, col_valor = df_ext.columns[0], df_ext.columns[1], df_ext.columns[2]
                    
            if col_data and col_desc and col_valor:
                for _, row in df_ext.iterrows():
                    raw_dt = str(row.get(col_data, '')).strip()
                    raw_desc = str(row.get(col_desc, '')).strip().upper()
                    raw_val = str(row.get(col_valor, '')).strip()
                    
                    if any(x in raw_desc for x in ["SALDO FINAL", "TOTAL ACUMULADOR", "SALDO INICIAL"]): continue
                    
                    dt_obj = converter_data_dominio(raw_dt)
                    if not dt_obj: continue
                    
                    val_float = limpar_valor(raw_val)
                    if abs(val_float) <= 0: continue
                    
                    is_debito = "-" in raw_val or "PAGTO" in raw_desc or "DEBITO" in raw_desc
                    is_credito = "+" in raw_val or any(x in raw_desc for x in ["RECEBID", "DEVOLU", "ESTORNO", "CREDIT", "RECB"])
                    if "TRANSFERENCIA" in raw_desc and not is_debito: is_credito = True
                        
                    transacoes.append({
                        'Data': dt_obj.strftime('%d/%m/%Y'),
                        'Total': abs(val_float),
                        'Fav': raw_desc,
                        'Is_Credito': is_credito if is_credito else not is_debito
                    })
        except Exception as e: st.error(f"Erro ao ler planilha de extrato: {e}")
            
    return transacoes

def gerar_txt_dominio_delimitado(df_final, incluir_cabecalho=False):
    linhas = []
    if incluir_cabecalho:
        header = "Data;Cód. Conta Debito;Cód. Conta Credito;Valor;Cód. Histórico;Complemento Histórico;Inicia Lote;Código Matriz/Filial;Centro de Custo Débito;Centro de Custo Crédito"
        linhas.append(header)
    
    for idx, row in df_final.iterrows():
        val_float = limpar_valor(row['Saídas'])
        if val_float <= 0: continue
        
        hist_texto = str(row.get('Histórico', '')).strip().replace(';', ',').replace('\r', '').replace('\n', ' ')
        if hist_texto.lower() == 'nan': hist_texto = ""
        
        cod_deb = str(row.get('Deb', '')).strip()
        if cod_deb.endswith('.0'): cod_deb = cod_deb[:-2]
        
        cod_cred = str(row.get('Cred', '')).strip()
        if cod_cred.endswith('.0'): cod_cred = cod_cred[:-2]
        
        if cod_deb.lower() in ['-', 'nan', 'none', '']: cod_deb = ""
        if cod_cred.lower() in ['-', 'nan', 'none', '']: cod_cred = ""
        if cod_deb == "" and cod_cred == "" and val_float == 0: continue
        
        val_str = f"{val_float:.2f}".replace('.', ',')
        data_str = str(row.get('Data', '')).strip()
        
        linha = f"{data_str};{cod_deb};{cod_cred};{val_str};;{hist_texto};;;;"
        linhas.append(linha)
        
    return "\r\n".join(linhas) + "\r\n"

# --- INTERFACE ---
with st.sidebar:
    st.header("⚙️ Parâmetros Contábeis")
    cod_banco_txt = st.text_input("Código da Conta Bancária:", value="1857")
    incluir_cabecalho = st.checkbox("Incluir Cabeçalho no TXT", value=False)
    st.divider()
    ignorar_data = st.checkbox("Ignorar Validação de Datas", value=True)
    tolerancia_dias = 99999 if ignorar_data else st.slider("Tolerância de Dias:", 0, 30, 7)
    ignorar_txt = st.text_area("Filtros de Exclusão do Extrato:", "SALDO INICIAL, SALDO FINAL, TOTAL ACUMULADOR")
    termos_ignorar = [t.strip().upper() for t in ignorar_txt.split(',')]

tab1, tab2 = st.tabs(["🔄 1. Nova Conciliação", "📤 2. Gerar TXT de Planilha Editada"])

with tab1:
    st.markdown("### Processe os relatórios e gere o cruzamento inicial")
    col1, col2, col3 = st.columns(3)
    with col1: f_fiscal = st.file_uploader("📂 Relatório de Entradas (Fiscal)", type=["xlsx","csv"])
    with col2: f_fornec = st.file_uploader("🗂️ Arquivo de Fornecedores", type=["xlsx","xls","csv"])
    with col3: f_extratos = st.file_uploader("📄 Extrato Bancário (PDF/Excel/CSV)", type=["pdf","xlsx","xls","csv"], accept_multiple_files=True)

    if f_fiscal and f_fornec and f_extratos:
        if f_fornec.name.endswith('.csv'):
            try: df_forn_raw = pd.read_csv(f_fornec, header=None, dtype=str, sep=None, engine='python')
            except: df_forn_raw = pd.read_csv(f_fornec, header=None, dtype=str)
        else:
            df_forn_raw = pd.read_excel(f_fornec, header=None, dtype=str)
            
        fornec_map_bd = {}
        for _, r in df_forn_raw.iterrows():
            row_vals = [str(x).strip() for x in r.values if pd.notna(x)]
            if len(row_vals) >= 2:
                v1, v2 = row_vals[0], row_vals[1]
                if any(c.isalpha() for c in v2) and not any(c.isalpha() for c in v1): cod, nome = v1, v2
                elif any(c.isalpha() for c in v1) and not any(c.isalpha() for c in v2): cod, nome = v2, v1
                else: cod, nome = v1, v2
                    
                cod = cod.split('.')[0]
                nome = nome.upper()
                if cod and nome and cod.lower() not in ['código', 'codigo', 'conta']:
                    fornec_map_bd[normalizar_para_match(nome)] = {"cod": cod, "nome": nome}

        df_fiscal_bruto = carregar_fiscal_seguro(f_fiscal)
        entries_list = []
        current_entry = None
        
        for idx, row in df_fiscal_bruto.iterrows():
            cod_lanc = str(row.get('codigo_lancamento', '')).strip()
            tipo_imp = str(row.get('tipo_imposto', '')).strip().upper()
            v_imp = abs(limpar_valor(row.get('valor_imposto', 0)))
            
            if cod_lanc.isdigit():
                v_bruto = abs(limpar_valor(row.get('valor_total', 0)))
                dt_obj = converter_data_dominio(row.get('data'))
                nome_f = str(row.get('nome_fornecedor', '')).strip().upper()
                
                current_entry = {
                    'data_f': dt_obj.strftime('%d/%m/%Y') if dt_obj else '-',
                    'dt_obj': dt_obj,
                    'nota': str(row.get('doc', '-')).split('.')[0],
                    'cod_f': str(row.get('codigo_fornecedor_doc', '-')).split('.')[0],
                    'name_f': nome_f,
                    'valor_bruto': v_bruto,
                    'irrf': v_imp if 'IRRF' in tipo_imp else 0.0,
                    'crf': v_imp if 'CRF' in tipo_imp else 0.0,
                    'matched': False
                }
                entries_list.append(current_entry)
            elif current_entry is not None and not cod_lanc.isdigit():
                if 'IRRF' in tipo_imp: current_entry['irrf'] += v_imp
                elif 'CRF' in tipo_imp: current_entry['crf'] += v_imp

        extrato_lista = []
        for f in f_extratos:
            extrato_lista.extend(extrair_dados_extrato(f, termos_ignorar))

        matriz_saida = []
        red_banco = cod_banco_txt.strip() 

        for trans in extrato_lista:
            fav_banco_norm = normalizar_para_match(trans['Fav'])
            v_banco = trans['Total']
            is_credito = trans['Is_Credito']
            
            match_fiscal = None
            for ent in entries_list:
                if ent['matched'] or (ent['valor_bruto'] != 0.0 and is_credito): continue 
                nome_f_norm = normalizar_para_match(ent['name_f'])
                nome_bate = (nome_f_norm in fav_banco_norm) or (fav_banco_norm in nome_f_norm)
                v_liquido = round(ent['valor_bruto'] - ent['irrf'] - ent['crf'], 2)
                val_match = (abs(round(v_banco, 2) - round(ent['valor_bruto'], 2)) <= 0.1) or (abs(round(v_banco, 2) - v_liquido) <= 0.1)
                
                if val_match and nome_bate:
                    match_fiscal = ent
                    ent['matched'] = True
                    break

            nome_banco_limpo, nota_banco = extrair_nome_nota_banco(trans['Fav'])

            if match_fiscal:
                fornecedor_final = match_fiscal['name_f']
                nota_final = match_fiscal['nota'] if match_fiscal['nota'] != '-' else nota_banco
                cod_forn_final, _ = buscar_codigo_fornecedor(fornecedor_final, fornec_map_bd)
            else:
                cod_forn_final, nome_dict = buscar_codigo_fornecedor(nome_banco_limpo, fornec_map_bd)
                fornecedor_final = nome_dict if nome_dict else nome_banco_limpo
                nota_final = nota_banco

            # 🛠️ CONSTRUÇÃO DO HISTÓRICO PADRONIZADO EXIGIDO
            prefixo = "RECB" if is_credito else "PAGTO"
            meio = f"NF {nota_final}" if nota_final else ""
            historico_padrao = f"{prefixo} {meio} {fornecedor_final}".replace("  ", " ").strip()

            if is_credito:
                # Inteligência Contábil: Se for transferência própria/interna, automatiza a conta 1121
                if any(x in fornecedor_final.upper() for x in ["TRANSFERENCIA", "INTERNA", "PROPRIETARIA", "TRANSACIONAL"]) and not cod_forn_final:
                    cod_credito = "1121"
                else:
                    cod_credito = cod_forn_final
                    
                matriz_saida.append({
                    'Data': trans['Data'], 'Deb': red_banco, 'Cred': cod_credito, 'Saídas': v_banco, 'Histórico': historico_padrao
                })
            else:
                matriz_saida.append({
                    'Data': trans['Data'], 'Deb': cod_forn_final, 'Cred': red_banco, 'Saídas': v_banco, 'Histórico': historico_padrao
                })

        colunas_leiaute = ['Data', 'Deb', 'Cred', 'Saídas', 'Histórico']
        if not matriz_saida:
            df_final = pd.DataFrame(columns=colunas_leiaute)
        else:
            df_final = pd.DataFrame(matriz_saida, columns=colunas_leiaute)

        df_final = sanitize_dataframe_for_excel(df_final)
        df_display = df_final.copy()
        df_display['Saídas'] = df_display['Saídas'].apply(formatar_moeda_br)
        st.dataframe(df_display, use_container_width=True)
        
        output_excel = io.BytesIO()
        with pd.ExcelWriter(output_excel, engine='openpyxl') as writer:
            df_final.to_excel(writer, index=False, sheet_name='Conciliação')
            
        txt_com_cabecalho = gerar_txt_dominio_delimitado(df_final, incluir_cabecalho=True).encode('iso-8859-1', errors='replace')
        txt_sem_cabecalho = gerar_txt_dominio_delimitado(df_final, incluir_cabecalho=False).encode('iso-8859-1', errors='replace')
        
        st.markdown("---")
        c_btn1, c_btn2, c_btn3 = st.columns(3)
        with c_btn1:
            st.download_button(label="📥 Baixar Planilha (.XLSX)", data=output_excel.getvalue(), file_name=f"Conciliacao_{datetime.now().strftime('%Y%m%d')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)
        with c_btn2:
            st.download_button(label="📄 Baixar TXT (SEM Cabeçalho)", data=txt_sem_cabecalho, file_name=f"Dominio_SEM_Cabecalho_{datetime.now().strftime('%Y%m%d')}.txt", mime="text/plain", use_container_width=True)
        with c_btn3:
            st.download_button(label="📄 Baixar TXT (COM Cabeçalho)", data=txt_com_cabecalho, file_name=f"Dominio_COM_Cabecalho_{datetime.now().strftime('%Y%m%d')}.txt", mime="text/plain", use_container_width=True)

with tab2:
    st.markdown("### Importe sua planilha auditada para gerar o arquivo final")
    f_editado = st.file_uploader("📥 1. Anexe a Planilha Editada", type=["xlsx"])
    f_fornec_tab2 = st.file_uploader("🗂️ 2. (Opcional) Cadastro Fornecedores", type=["xlsx","xls","csv"])
    
    if f_editado:
        try:
            df_editado = pd.read_excel(f_editado, dtype=str)
            if f_fornec_tab2:
                if f_fornec_tab2.name.endswith('.csv'): df_f2 = pd.read_csv(f_fornec_tab2, header=None, dtype=str)
                else: df_f2 = pd.read_excel(f_fornec_tab2, header=None, dtype=str)
                    
                fmap2 = {}
                for _, r in df_f2.iterrows():
                    row_vals = [str(x).strip() for x in r.values if pd.notna(x)]
                    if len(row_vals) >= 2:
                        v1, v2 = row_vals[0], row_vals[1]
                        if any(c.isalpha() for c in v2): cod, nome = v1, v2
                        else: cod, nome = v2, v1
                        fmap2[normalizar_para_match(nome)] = {"cod": cod.split('.')[0], "nome": nome.upper()}
                
                for idx, row in df_editado.iterrows():
                    cod_deb = str(row.get('Deb', '')).strip()
                    if cod_deb.endswith('.0'): cod_deb = cod_deb[:-2]
                    if len(cod_deb) == 3 and cod_deb.isdigit(): df_editado.at[idx, 'Deb'] = ""
                    
                    if str(df_editado.at[idx, 'Deb']).strip().lower() in ['', 'nan', 'none', '-']:
                        hist = str(row.get('Histórico', ''))
                        novo_cod, _ = buscar_codigo_fornecedor(hist, fmap2)
                        if novo_cod: df_editado.at[idx, 'Deb'] = novo_cod

            df_editado = df_editado[df_editado['Histórico'].astype(str).str.upper().str.contains("PAGTO|RECB")]
            df_editado = sanitize_dataframe_for_excel(df_editado)
            st.dataframe(df_editado, use_container_width=True)
            
            txt_com_cabecalho_editado = gerar_txt_dominio_delimitado(df_editado, incluir_cabecalho=True).encode('iso-8859-1', errors='replace')
            txt_sem_cabecalho_editado = gerar_txt_dominio_delimitado(df_editado, incluir_cabecalho=False).encode('iso-8859-1', errors='replace')
            
            c_btn1_e, c_btn2_e = st.columns(2)
            with c_btn1_e: st.download_button(label="📄 Baixar TXT Editado (SEM Cabeçalho)", data=txt_sem_cabecalho_editado, file_name="Dominio_Editado_SEM.txt", mime="text/plain", use_container_width=True)
            with c_btn2_e: st.download_button(label="📄 Baixar TXT Editado (COM Cabeçalho)", data=txt_com_cabecalho_editado, file_name="Dominio_Editado_COM.txt", mime="text/plain", use_container_width=True)
        except Exception as e: st.error(f"Erro: {e}")
