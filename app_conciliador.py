import streamlit as st
import pandas as pd
import re
import io
import csv
import warnings
from datetime import datetime

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

def normalizar_para_match(texto):
    if not texto: return ""
    return re.sub(r'[\s\-,.\/]', '', str(texto).upper().strip())

# Varredura inteligente para pular linhas de metadados do Domínio
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
                            sub_valores = [v for v in sub_valores_raw if re.search(r'\d+,\d{2}', v)]
                            
                            min_len = min(len(sub_datas), len(sub_descs))
                            for k in range(min_len):
                                data_match = re.search(r'(\d{2}/\d{2}/\d{4})', sub_datas[k])
                                if not data_match: continue
                                desc_txt = sub_descs[k].upper()
                                if any(x in desc_txt for x in ["SALDO INICIAL", "SALDO FINAL", "TOTAL ACUMULADOR", "RESUMO"]): continue
                                if any(t in desc_txt for t in termos_ignorar if t): continue
                                
                                is_credito = any(x in desc_txt for x in ["RECEBID", "DEVOLU", "ESTORNO", "CREDITO", "CRÉDITO", "DEPÓSITO", "TED RECEBIDA"])
                                val_final = abs(limpar_valor(sub_valores[k])) if k < len(sub_valores) else 0.0
                                
                                if val_final > 0:
                                    for t in ["PAGAMENTO VIA PIX", "PAGAMENTO DE BOLETO", "TRANSFERENCIA INTERNA", "R$", "RS"]:
                                        desc_txt = desc_txt.replace(t, '')
                                    desc_txt = normalizar_espacos(desc_txt).strip('," ')
                                    transacoes.append({'Data': data_match.group(1), 'Total': val_final, 'Fav': desc_txt if desc_txt else "MOVIMENTO BANCARIO", 'Is_Credito': is_credito})
                    else:
                        for linha in texto_bruto.split('\n'):
                            linha_upper = linha.upper()
                            if any(x in linha_upper for x in ["SALDO INICIAL", "SALDO FINAL", "TOTAL ACUMULADOR"]): continue
                            data_match = re.search(r'(\d{2}/\d{2}/\d{4})', linha)
                            valor_match = re.findall(r'-?[\d.]*,\d{2}', linha)
                            if data_match and valor_match:
                                is_credito = any(x in linha_upper for x in ["RECEBID", "DEVOLU", "ESTORNO", "CREDIT"])
                                val = abs(limpar_valor(valor_match[0]))
                                desc_bruta = linha.replace(data_match.group(1), "")
                                for v_txt in valor_match: desc_bruta = desc_bruta.replace(v_txt, "")
                                if val > 0:
                                    transacoes.append({'Data': data_match.group(1), 'Total': val, 'Fav': normalizar_espacos(desc_bruta).strip('," '), 'Is_Credito': is_credito})
        except Exception as e: st.error(f"Erro ao ler PDF: {e}")
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
    ignorar_txt = st.text_area("Filtros de Exclusão do Extrato:", "SALDO INICIAL, SALDO FINAL, TRANSFERENCIA INTERNA ENTRE CONTAS")
    termos_ignorar = [t.strip().upper() for t in ignorar_txt.split(',')]

col1, col2, col3 = st.columns(3)
with col1: f_fiscal = st.file_uploader("📂 1. Relatório de Entradas (Fiscal)", type=["xlsx","csv"])
with col2: f_fornec = st.file_uploader("🗂️ 2. Cadastro FORNEC BET DA SORTE (.csv/.xls)", type=["xlsx","xls","csv"])
with col3: f_extratos = st.file_uploader("📄 3. Extrato Bancário em PDF", type=["pdf"], accept_multiple_files=True)

if f_fiscal and f_fornec and f_extratos:
    # 1. Carrega Cadastro de Fornecedores para obter a Conta Contábil Correta
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

    # 2. Carrega e limpa o Relatório Fiscal de Entradas
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
                'irrf': 0.0, 'crf': 0.0
            }
            entries_list.append(current_entry)
        elif current_entry is not None and pd.isna(row.get('codigo_lancamento')) and pd.notna(row.get('tipo_imposto')):
            tipo = str(row.get('tipo_imposto')).strip().upper()
            v_imp = abs(limpar_valor(row.get('valor_imposto', 0)))
            if 'IRRF' in tipo: current_entry['irrf'] = v_imp
            elif 'CRF' in tipo: current_entry['crf'] = v_imp

    # 3. Processa Extratos Bancários (Chamada CORRIGIDA)
    extrato_lista = []
    for f in f_extratos:
        extrato_lista.extend(extrair_dados_extrato(f, termos_ignorar))

    # --- MATRIZ DE CONFRONTO UNIFICADA ---
    matriz_saida = []
    ids_extrato_usados = set()
    red_banco = "8281458" # Conta Reduzida fixa do Z.ro Bank

    for ent in entries_list:
        name_norm = normalizar_para_match(ent['name_f'])
        v_bruto = ent['valor_bruto']
        v_liquido_esperado = v_bruto - ent['irrf'] - ent['crf']
        
        match_banco = None
        for i, trans in enumerate(extrato_lista):
            if i in ids_extrato_usados: continue
            fav_norm = normalizar_para_match(trans['Fav'])
            
            nome_bate = (name_norm[:10] in fav_norm) or (fav_norm[:10] in name_norm)
            try:
                dt_banco = datetime.strptime(trans['Data'], '%d/%m/%Y').date()
                dif_dias = abs((ent['dt_obj'] - dt_banco).days) if ent['dt_obj'] else 999
            except: dif_dias = 999
            
            if dif_dias <= tolerancia_dias:
                if v_bruto == 0.0 and nome_bate and not trans['Is_Credito']:
                    match_banco = trans; ids_extrato_usados.add(i); break
                elif (abs(trans['Total'] - v_bruto) < 0.1 or abs(trans['Total'] - v_liquido_esperado) < 0.1) and nome_bate:
                    match_banco = trans; ids_extrato_usados.add(i); break

        # Resgata o código do Fornecedor direto do arquivo do BD de Contas
        cod_forn_final = fornec_map_bd.get(name_norm, ent['cod_f'])
        if not cod_forn_final or cod_forn_final == '-': cod_forn_final = ent['cod_f']

        if match_banco:
            is_pagto = "PAGTO" if not match_banco['Is_Credito'] else "RECB"
            matriz_saida.append({
                'Data': ent['data_f'], 'Deb': cod_forn_final if is_pagto == "PAGTO" else red_banco,
                'Cred': red_banco if is_pagto == "PAGTO" else "4101", 'Valor': v_bruto if v_bruto > 0 else match_banco['Total'],
                'Hist': f"VLR REF CONCILIACAO NF {ent['nota']} - {ent['name_f']}", 'Data do PAGTO': match_banco['Data'],
                'Cod forn Cont': cod_forn_final, 'Conta Red Banco': red_banco, 'Saída': match_banco['Total'] if is_pagto == "PAGTO" else 0.0,
                'se é PAGTO OU RECB': is_pagto, 'N° da Nota': ent['nota'], 'Raz Social': ent['name_f']
            })
        else:
            matriz_saida.append({
                'Data': ent['data_f'], 'Deb': cod_forn_final, 'Cred': '-', 'Valor': v_bruto,
                'Hist': f"NF PENDENTE APENAS NO FISCAL (SEM DEBITO EM CONTA) - {ent['name_f']}", 'Data do PAGTO': '-',
                'Cod forn Cont': cod_forn_final, 'Conta Red Banco': red_banco, 'Saída': 0.0,
                'se é PAGTO OU RECB': 'PAGTO', 'N° da Nota': ent['nota'], 'Raz Social': ent['name_f']
            })

    # Sobras do Extrato Bancário (Movimentações Financeiras sem Nota Fiscal)
    for i, trans in enumerate(extrato_lista):
        if i not in ids_extrato_usados:
            fav_norm = normalizar_para_match(trans['Fav'])
            cod_forn_final = fornec_map_bd.get(fav_norm, '-')
            
            is_pagto = "PAGTO" if not trans['Is_Credito'] else "RECB"
            matriz_saida.append({
                'Data': trans['Data'], 'Deb': cod_forn_final if (is_pagto == "PAGTO" and cod_forn_final != '-') else '9999',
                'Cred': red_banco if is_pagto == "PAGTO" else '4101', 'Valor': trans['Total'],
                'Hist': f"MOVIMENTO BANCARIO SEM NOTA FISCAL LANCADA - {trans['Fav']}", 'Data do PAGTO': trans['Data'],
                'Cod forn Cont': cod_forn_final, 'Conta Red Banco': red_banco, 'Saída': trans['Total'] if is_pagto == "PAGTO" else 0.0,
                'se é PAGTO OU RECB': is_pagto, 'N° da Nota': '-', 'Raz Social': trans['Fav']
            })

    df_final = pd.DataFrame(matriz_saida)
    
    # Reordenação exata requerida pelo leiaute de colunas do usuário (com Saída no singular)
    colunas_leiaute = ['Data', 'Deb', 'Cred', 'Valor', 'Hist', 'Data do PAGTO', 'Cod forn Cont', 'Conta Red Banco', 'Saída', 'se é PAGTO OU RECB', 'N° da Nota', 'Raz Social']
    df_final = df_final[colunas_leiaute]

    # Grid Visual Formatado para Monitoramento na Tela
    df_display = df_final.copy()
    for col in ['Valor', 'Saída']:
        df_display[col] = df_display[col].apply(formatar_moeda)

    st.dataframe(df_display, use_container_width=True)
    
    # Geração nativa e blindada do arquivo .xlsx
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_final.to_excel(writer, index=False, sheet_name='Conciliado_Unificado')
    
    st.download_button(
        label="📥 Baixar Planilha de Conciliação Requerida (.XLSX)",
        data=output.getvalue(),
        file_name=f"Conciliacao_Unificada_BetSorte_{datetime.now().strftime('%Y%m%d')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
