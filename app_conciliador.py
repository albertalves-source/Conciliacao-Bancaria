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
    for termo in ["LTDA", "SA", "S/A", "ME", "EIRELI", "FILHO", "PARTICIPACOES", "SERVICOS", "COMERCIO", "MARKETING", "DIGITAL", "TECNOLOGIA"]:
        txt = txt.replace(termo, "")
    return txt

def limpar_historico_banco(texto, is_credito=False):
    t = str(texto).upper()
    
    # --- LIMPEZA DE LIXO BANCÁRIO DA FLABET/PIX ---
    t = re.sub(r'\bE\d{14}[A-Z0-9]*\b', '', t) # PIX IDs End-to-End
    prefixos = r'(?:PAGTO|RECB|PG\.|D[EÉ]BITO TRANSFERE|D[EÉ]BITO TRANFEREN|PIX ENVIADO|CR[EÉ]DITO PIX RECEBIDO|PIX RECEBIDO|CR[EÉ]DITO TRANSFERE|CR[EÉ]DITO DEVOLU[CÇ][AÃ]O|DESCONTO DE|TRANSFER[EÊ]NCIA INTERNA|PAGAMENTO DE BOLETO|D[EÉ]BITO|CR[EÉ]DITO)'
    sufixos = r'(?:CELCOIN IP|CELCOIN|BANCO ISPB|ISPB|DELBANK|C6 BANK|DOCK IP S\.A\.?|DOCK IP|BCO DO|BCO\b)'
    t = re.sub(prefixos, '', t).strip()
    t = re.sub(sufixos, '', t).strip()
    t = re.sub(r'\b[A-F0-9]{4,10}\b', '', t) # Hashes hexadecimais do PIX
    t = re.sub(r'\b\d{4}\s+\d{8,15}\s+\d{6,10}\b', '', t) # Agências e contas em bloco
    t = re.sub(r'\b\d{5,}\b', '', t) # Números soltos gigantes
    t = re.sub(r'\b\d{2}/\d{2}/\d{2,4}.*', '', t) # Pedaços de data que sobram no final
    # ---------------------------------------------

    t = re.sub(r'\b\d{11}\b|\b\d{14}\b', '', t) 
    t = re.sub(r'\b[A-Z0-9]{25,35}\b', '', t, flags=re.IGNORECASE)
    
    termos = [
        "PAGAMENTO VIA PIX", "PAGAMENTO DE BOLETO", "PIX DE MESMA TITULARIDADE.", "PIX DE MESMA TITULARIDADE", 
        "PIX DEVOLVIDO RECEBIDO", "TRANSFERENCIA INTERNA ENTRE CONTAS", 
        "TRANSFERENCIA INTERNA", "TED RECEBIDA", "(PIXSENDSELF)", "RECEBIMENTO"
    ]
    for termo in termos: t = t.replace(termo, '')
        
    bancos = [
        "BCO DO BRASIL S.A.", "CAIXA ECONOMICA FEDERAL", "BANCO INTER", 
        "ITAÚ UNIBANCO S.A.", "BCO SANTANDER (BRASIL) S.A.", "BANCO BTG PACTUAL S.A.", 
        "STONE IP S.A.", "BCO BRADESCO S.A.", "NU PAGAMENTOS - IP", "NU PAGAMENTOS IP", 
        "BCO C6 S.A.", "DOCK IP S.A.", "ASAAS IP S.A.", "DELCRED SCD S.A.", "SICREDI RECIFE", 
        "CCLA SUDOESTE GOIANO", "MERCADO PAGO IP LTDA.", "CCLA DA PARAÍBA - SICOOB PARAÍBA", 
        "PAGSEGURO INTERNET IP S.A.", "FITS IP", "CORA SCFI", "ACG IP S.A."
    ]
    for banco in bancos: t = t.replace(banco, '')
        
    t = re.sub(r'(?:-\s*)?(?:R\$|RS)\s*\d*(?:\s*(?:R\$|RS))?', '', t, flags=re.IGNORECASE)
    t = t.replace('-', ' ')
    t = re.sub(r'\s{2,}', ' ', t)
    t = normalizar_espacos(t).strip(' ,"-.')
    
    if not t or t == "":
        t = "TRANSFERENCIA INTERNA ENTRE CONTAS" if is_credito else "PAGAMENTO DE BOLETO"
        
    return t

def buscar_codigo_fornecedor(nome_pesquisa, dicionario_fornecedores):
    if not nome_pesquisa: return ""
    
    nome_limpo = str(nome_pesquisa).upper()
    nome_limpo = re.sub(r'^(PAGTO|RECB|PG\.)\s*', '', nome_limpo)
    nome_limpo = re.sub(r'^(A\s+)', '', nome_limpo)
    nome_limpo = re.sub(r'^(NF\s*\d+\s*)', '', nome_limpo)
    nome_limpo = re.sub(r'\b\d{2}\.?\d{3}\.?\d{3}\/?\d{4}-?\d{2}\b', '', nome_limpo) 
    nome_limpo = re.sub(r'\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b', '', nome_limpo) 
    nome_limpo = re.sub(r'\b\d{8}\b|\b\d{2}\.\d{3}\.\d{3}\b', '', nome_limpo) 
    
    nome_pesquisa_norm = normalizar_para_match(nome_limpo)
    if not nome_pesquisa_norm: return ""
    
    if nome_pesquisa_norm in dicionario_fornecedores: 
        return dicionario_fornecedores[nome_pesquisa_norm]
        
    for nome_bd_norm, codigo in dicionario_fornecedores.items():
        if nome_pesquisa_norm.startswith(nome_bd_norm) or nome_bd_norm.startswith(nome_pesquisa_norm):
            return codigo

    if len(nome_pesquisa_norm) >= 10:
        for nome_bd_norm, codigo in dicionario_fornecedores.items():
            if (nome_pesquisa_norm in nome_bd_norm) or (nome_bd_norm in nome_pesquisa_norm):
                return codigo
                
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
