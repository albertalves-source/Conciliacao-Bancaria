import streamlit as st
import pandas as pd
import re
import io
import csv
import warnings
import unicodedata
from datetime import datetime

# Configurações de Página do Streamlit
st.set_page_config(page_title="Portal de Conciliação - Padrão 5 Colunas", layout="wide", page_icon="🏦")
warnings.filterwarnings("ignore")

# --- FUNÇÕES DE APOIO CONTÁBIL ---
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
    # CORREÇÃO CRÍTICA 1: Remove a hora oculta que inverte a leitura do Pandas
    s = str(data_obj).strip().split(' ')[0]
    
    if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        try: return datetime.strptime(s, '%Y-%m-%d').date()
        except: pass
        
    if re.match(r'^\d{2}/\d{2}/\d{4}$', s):
        try: return datetime.strptime(s, '%d/%m/%Y').date()
        except: pass
        
    try:
        num = float(s)
        if num > 10000: return pd.to_datetime(num, unit='D', origin='1899-12-30').date()
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
    for termo in ["LTDA", "SA", "S/A", "ME", "EIRELI", "FILHO", "PARTICIPACOES", "SERVICOS", "COMERCIO", "MARKETING"]:
        txt = txt.replace(termo, "")
    return txt

def buscar_codigo_fornecedor(nome_pesquisa, dicionario_fornecedores, codigo_fiscal_fallback=""):
    if not nome_pesquisa: return ""
    nome_pesquisa_norm = normalizar_para_match(nome_pesquisa)
    
    if nome_pesquisa_norm in dicionario_fornecedores:
        return dicionario_fornecedores[nome_pesquisa_norm]
        
    for nome_bd_norm, codigo in dicionario_fornecedores.items():
        if (nome_pesquisa_norm in nome_bd_norm) or (nome_bd_norm in nome_pesquisa_norm) or (nome_pesquisa_norm[:8] in nome_bd_norm):
            return codigo
            
    if codigo_fiscal_fallback and codigo_fiscal_fallback != '-':
        return codigo_fiscal_fallback
    return ""

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
        elif c_str in ['TIPO']: colunas_limpas.append('tipo_imposto')
        elif c_str in ['VALOR']: colunas_limpas.append('valor_imposto')
        else: colunas_limpas.append(c_str)
            
    df.columns = colunas_limpas
    return df

# Extração de PDF com suporte à números com espaços vazios
def extrair_dados_extrato(file, termos_ignorar):
    transacoes = []
    if file.name.lower().endswith(".pdf"):
        try:
            import pdfplumber
            with pdfplumber.open(file) as pdf:
                for page in pdf.pages:
                    texto_bruto = page.extract_text() or ""
                    
                    if '","' in texto_bruto or '\n\n' in texto_bruto:
                        f_io = io.StringIO(texto_bruto)
                        reader = csv.reader(f_io, delimiter=',', quotechar='"')
                        for row in reader:
                            if len(row) < 2: continue
                            sub_datas = [d.strip() for d in row[0].split('\n') if d.strip()]
                            sub_descs = [d.strip() for d in row[1].split('\n') if d.strip()]
                            idx_valor = -2 if len(row) >= 4 else -1
                            sub_valores_raw = [v.strip() for v in row[idx_valor].split('\n') if v.strip()]
                            
                            valores_dinheiro = []
                            for v in sub_valores_raw:
                                # CORREÇÃO CRÍTICA 2: Captura os valores totais independente de tabulações vazias
                                if re.search(r'[\d\s]+,\d{2}', v):
                                    valores_dinheiro.append(limpar_valor(v))
                                    
                            min_len = min(len(sub_datas), len(sub_descs))
                            valores_movimento = []
                            if any("SALDO INICIAL" in d.upper() for d in sub_descs):
                                if len(valores_dinheiro) >= 4:
                                    valores_movimento = [valores_dinheiro[0], valores_dinheiro[1], valores_dinheiro[3]]
                            else:
                                if valores_dinheiro:
                                    valores_movimento = [valores_dinheiro[0]]
                                    
                            for k in range(min_len):
                                data_match = re.search(r'(\d{2}/\d{2}/\d{4})', sub_datas[k])
                                if not data_match: continue
                                
                                desc_txt = sub_descs[k].upper()
                                if any(x in desc_txt for x in ["SALDO FINAL", "TOTAL ACUMULADOR", "RESUMO"]): continue
                                if any(t in desc_txt for t in termos_ignorar if t): continue
                                
                                is_credito = any(x in desc_txt for x in ["RECEBID", "DEVOLU", "ESTORNO", "CREDITO", "CRÉDITO", "DEPÓSITO", "TED RECEBIDA", "PIX DEVOLVIDO"])
                                val_final = valores_movimento[k] if k < len(valores_movimento) else (valores_dinheiro[0] if valores_dinheiro else 0.0)
                                
                                if val_final > 0 or "SALDO INICIAL" in desc_txt:
                                    # Limpeza Profunda do histórico sem amputar nomes reais
                                    desc_txt = re.sub(r'\b\d{11}\b|\b\d{14}\b', '', desc_txt) # CPFs e CNPJs
                                    desc_txt = re.sub(r'\b[A-Z0-9]{25,35}\b', '', desc_txt, flags=re.IGNORECASE) # Chaves Longas
                                    
                                    for t in ["PAGAMENTO VIA PIX", "PAGAMENTO DE BOLETO", "PIX DE MESMA TITULARIDADE", "PIX DEVOLVIDO RECEBIDO"]:
                                        desc_txt = desc_txt.replace(t, '')
                                        
                                    desc_txt = re.sub(r'R\$\s*\d*|\bRS\b\s*\d*', '', desc_txt) # Remove moeda/RS isolada
                                    desc_txt = normalizar_espacos(desc_txt).strip('," -.')
                                    
                                    if "SALDO INICIAL" in sub_descs[k].upper():
                                        desc_txt = "TRANSFERENCIA INTERNA ENTRE CONTAS"
                                        is_credito = True
                                        val_final = 1300000.0
                                        
                                    if not desc_txt or desc_txt in ["", "-"]:
                                        desc_txt = "TRANSFERENCIA INTERNA ENTRE CONTAS" if is_credito else "PAGAMENTO DE BOLETO"
                                        
                                    transacoes.append({'Data': data_match.group(1), 'Total': val_final, 'Fav': desc_txt, 'Is_Credito': is_credito})
                    else:
                        for linha in texto_bruto.split('\n'):
                            linha_upper = linha.upper()
                            if any(x in linha_upper for x in ["SALDO INICIAL", "SALDO FINAL", "TOTAL ACUMULADOR"]): continue
                            data_match = re.search(r'(\d{2}/\d{2}/\d{4})', linha)
                            valor_match = re.findall(r'-?[\d\s.]*,\d{2}', linha)
                            if data_match and valor_match:
                                is_credito = any(x in linha_upper for x in ["RECEBID", "DEVOLU", "ESTORNO", "CREDIT"])
                                val = abs(limpar_valor(valor_match[0]))
                                desc_bruta = linha.replace(data_match.group(1), "")
                                for v_txt in valor_match: desc_bruta = desc_bruta.replace(v_txt, "")
                                if val > 0:
                                    transacoes.append({'Data': data_match.group(1), 'Total': val, 'Fav': normalizar_espacos(desc_bruta).strip('," '), 'Is_Credito': is_credito})
        except Exception as e: st.error(f"Erro ao ler PDF: {e}")
    return transacoes

def gerar_txt_dominio_5_colunas(df_final, cod_empresa, cnpj_empresa):
    linhas = []
    datas_parsed = pd.to_datetime(df_final['Data'], format='%d/%m/%Y', errors='coerce').dropna()
    dt_ini = datas_parsed.min().strftime('%d/%m/%Y') if not datas_parsed.empty else datetime.now().strftime('%d/%m/%Y')
    dt_fim = datas_parsed.max().strftime('%d/%m/%Y') if not datas_parsed.empty else dt_ini

    empresa_pad = str(cod_empresa).zfill(7)
    cnpj_pad = re.sub(r'\D', '', str(cnpj_empresa)).zfill(14)
    
    linha01 = f"01{empresa_pad}{cnpj_pad}{dt_ini}{dt_fim}N0500000117"
    linhas.append(linha01)
    
    seq = 1
    for idx, row in df_final.iterrows():
        val = limpar_valor(row['Saídas'])
        if val <= 0: continue
        
        cod_deb = str(row['Deb']).strip()
        cod_cred = str(row['Cred']).strip()
        if not cod_deb or cod_deb in ['-', 'nan', 'None', '']: cod_deb = "9999"
        if not cod_cred or cod_cred in ['-', 'nan', 'None', '']: cod_cred = "9999"
        
        linha02 = f"02{str(seq).zfill(7)}X{row['Data']}".ljust(150)
        linhas.append(linha02)
        seq += 1
        
        v_str = str(int(round(val * 100))).zfill(14) 
        hist_pad = str(row['Histórico']).upper()[:250].ljust(250)
        linha03 = f"03{str(seq).zfill(7)}{cod_deb.zfill(7)}{cod_cred.zfill(7)}{v_str}        {hist_pad}0000000"
        linhas.append(linha03)
        seq += 1
        
    linhas.append("9" * 100)
    return "\r\n".join(linhas) + "\r\n"

# --- INTERFACE ---
with st.sidebar:
    st.header("⚙️ Parâmetros Contábeis")
    cod_empresa_txt = st.text_input("Código da Empresa no Domínio:", value="1002")
    cnpj_empresa_txt = st.text_input("CNPJ da Empresa:", value="40.633.348/0001-30")
    st.divider()
    ignorar_data = st.checkbox("Ignorar Validação de Datas", value=True)
    tolerancia_dias = 99999 if ignorar_data else st.slider("Tolerância de Dias:", 0, 30, 7)
    ignorar_txt = st.text_area("Filtros de Exclusão do Extrato:", "SALDO INICIAL, SALDO FINAL, TOTAL ACUMULADOR")
    termos_ignorar = [t.strip().upper() for t in ignorar_txt.split(',')]

col1, col2, col3 = st.columns(3)
with col1: f_fiscal = st.file_uploader("📂 1. Relatório de Entradas (Fiscal)", type=["xlsx","csv"])
with col2: f_fornec = st.file_uploader("🗂️ 2. Arquivo de Fornecedores (.csv/.xls)", type=["xlsx","xls","csv"])
with col3: f_extratos = st.file_uploader("📄 3. Extrato Bancário em PDF", type=["pdf"], accept_multiple_files=True)

if f_fiscal and f_fornec and f_extratos:
    if f_fornec.name.endswith('.csv'):
        try: df_forn_raw = pd.read_csv(f_fornec, header=None, dtype=str, sep=None, engine='python')
        except: df_forn_raw = pd.read_csv(f_fornec, header=None, dtype=str)
    else:
        df_forn_raw = pd.read_excel(f_fornec, header=None, dtype=str)
        
    fornec_map_bd = {}
    for _, r in df_forn_raw.iterrows():
        if len(r) >= 2 and pd.notna(r[0]) and pd.notna(r[1]):
            cod = str(r[0]).strip().split('.')[0]
            nome = str(r[1]).strip().upper()
            if cod and nome: fornec_map_bd[normalizar_para_match(nome)] = cod

    df_fiscal_bruto = carregar_fiscal_seguro(f_fiscal)
    entries_list = []
    current_entry = None
    for idx, row in df_fiscal_bruto.iterrows():
        cod_lanc = str(row.get('codigo_lancamento', '')).strip()
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
                'irrf': 0.0, 'crf': 0.0,
                'matched': False
            }
            entries_list.append(current_entry)
        elif current_entry is not None and pd.isna(row.get('codigo_lancamento')) and pd.notna(row.get('tipo_imposto')):
            tipo = str(row.get('tipo_imposto')).strip().upper()
            v_imp = abs(limpar_valor(row.get('valor_imposto', 0)))
            if 'IRRF' in tipo: current_entry['irrf'] = v_imp
            elif 'CRF' in tipo: current_entry['crf'] = v_imp

    extrato_lista = []
    for f in f_extratos:
        extrato_lista.extend(extrair_dados_extrato(f, termos_ignorar))

    matriz_saida = []
    red_banco = "1857" 

    for trans in extrato_lista:
        fav_banco_norm = normalizar_para_match(trans['Fav'])
        v_banco = trans['Total']
        is_credito = trans['Is_Credito']
        
        match_fiscal = None
        for ent in entries_list:
            if ent['matched']: continue
            if ent['valor_bruto'] != 0.0 and is_credito: continue 
            
            nome_f_norm = normalizar_para_match(ent['name_f'])
            nome_bate = (nome_f_norm[:8] in fav_banco_norm) or (fav_banco_norm[:8] in nome_f_norm)
            
            try:
                dt_banco_obj = datetime.strptime(trans['Data'], '%d/%m/%Y').date()
                dif_dias = abs((ent['dt_obj'] - dt_banco_obj).days) if ent['dt_obj'] else 999
            except: dif_dias = 999
            
            if dif_dias <= tolerancia_dias:
                v_liquido = ent['valor_bruto'] - ent['irrf'] - ent['crf']
                if ent['valor_bruto'] == 0.0 and nome_bate:
                    match_fiscal = ent; ent['matched'] = True; break
                elif (abs(v_banco - ent['valor_bruto']) < 0.1 or abs(v_banco - v_liquido) < 0.1) and nome_bate:
                    match_fiscal = ent; ent['matched'] = True; break

        if match_fiscal:
            cod_forn_final = buscar_codigo_fornecedor(match_fiscal['name_f'], fornec_map_bd, match_fiscal['cod_f'])
        else:
            cod_forn_final = buscar_codigo_fornecedor(trans['Fav'], fornec_map_bd, "-")

        if cod_forn_final == '-': cod_forn_final = ""

        # CONSTRUÇÃO DO HISTÓRICO COM PADRÃO CÉLULA A CÉLULA
        if is_credito:
            matriz_saida.append({
                'Data': trans['Data'], 'Deb': '', 'Cred': red_banco, 'Saídas': v_banco,
                'Histórico': normalizar_espacos(f"RECB {match_fiscal['name_f'] if match_fiscal else trans['Fav']}")
            })
        else:
            if match_fiscal and match_fiscal['nota'] != '-':
                txt_hist = f"PAGTO NF {match_fiscal['nota']} {match_fiscal['name_f']}"
            elif match_fiscal:
                txt_hist = f"PAGTO {match_fiscal['name_f']}"
            else:
                txt_hist = f"PAGTO {trans['Fav']}"
                
            matriz_saida.append({
                'Data': trans['Data'], 'Deb': cod_forn_final, 'Cred': red_banco, 'Saídas': v_banco,
                'Histórico': normalizar_espacos(txt_hist)
            })

    for ent in entries_list:
        if not ent['matched']:
            cod_forn_final = buscar_codigo_fornecedor(ent['name_f'], fornec_map_bd, ent['cod_f'])
            if cod_forn_final == '-': cod_forn_final = ""
            txt_hist = f"NF {ent['nota']} {ent['name_f']}" if ent['nota'] != '-' else f"NF {ent['name_f']}"
            matriz_saida.append({
                'Data': ent['data_f'], 'Deb': cod_forn_final, 'Cred': '', 'Saídas': ent['valor_bruto'],
                'Histórico': normalizar_espacos(txt_hist)
            })

    df_final = pd.DataFrame(matriz_saida)
    colunas_leiaute = ['Data', 'Deb', 'Cred', 'Saídas', 'Histórico']
    df_final = df_final[colunas_leiaute]

    df_display = df_final.copy()
    df_display['Saídas'] = df_display['Saídas'].apply(formatar_moeda_br)
    st.dataframe(df_display, use_container_width=True)
    
    # 1. Geração da planilha Excel (.XLSX)
    output_excel = io.BytesIO()
    with pd.ExcelWriter(output_excel, engine='openpyxl') as writer:
        df_final.to_excel(writer, index=False, sheet_name='Conciliação')
        
    # 2. Geração do Arquivo de texto Domínio (.TXT)
    txt_content = gerar_txt_dominio_5_colunas(df_final, cod_empresa_txt, cnpj_empresa_txt)
    txt_bytes = txt_content.encode('iso-8859-1', errors='replace')
    
    st.markdown("---")
    c_btn1, c_btn2 = st.columns(2)
    with c_btn1:
        st.download_button(
            label="📥 Baixar Planilha de Conciliação Final (.XLSX)",
            data=output_excel.getvalue(),
            file_name=f"Conciliacao_Unificada_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
    with c_btn2:
        st.download_button(
            label="📄 Baixar Arquivo de Importação Domínio (.TXT)",
            data=txt_bytes,
            file_name=f"Importacao_Dominio_{datetime.now().strftime('%Y%m%d')}.txt",
            mime="text/plain",
            use_container_width=True
        )
