
import streamlit as st
import pandas as pd
import re
import io
import warnings
import unicodedata
from datetime import datetime

st.set_page_config(page_title="Portal de Conciliação Individual", layout="wide", page_icon="🏦")
warnings.filterwarnings("ignore")

# ================= UTIL =================

def formatar_moeda_br(v):
    try:
        return f"R$ {float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return "R$ 0,00"

def limpar_valor(v):
    if pd.isna(v):
        return 0.0

    s = str(v).upper().strip()
    s = s.replace("R$", "").replace("$", "").replace(" ", "")
    s = s.replace("C", "").replace("D", "")

    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")

    try:
        return abs(float(s))
    except:
        return 0.0

def formatar_valor_dominio(v):
    return f"{limpar_valor(v):.2f}".replace(".", ",")

def converter_data(data_obj):
    if pd.isna(data_obj):
        return None

    s = str(data_obj).strip().split(" ")[0]

    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except:
            pass

    try:
        return pd.to_datetime(float(s), unit="D", origin="1899-12-30").date()
    except:
        return None

def higienizar_texto_lista_palavras(texto):
    txt = str(texto).upper().strip()

    txt = ''.join(
        c for c in unicodedata.normalize('NFD', txt)
        if unicodedata.category(c) != 'Mn'
    )

    txt = re.sub(r'[^A-Z0-9\s]', ' ', txt)
    txt = re.sub(r'\s+', ' ', txt).strip()

    remover = {
        "LTDA","SA","S","ME","EIRELI",
        "SOCIEDADEUNIPESSOAL","SOLUCOESTECNOLOGICAS",
        "DESENVOLVEDORADESISTEMA","DESENVOLVEDORADESISTEMAS"
    }

    return [p for p in txt.split() if p and p not in remover]

def normalizar_para_match(texto):
    return "".join(higienizar_texto_lista_palavras(texto))

# ================= LEITURA =================

def ler_dataframe_upload(file):
    file.seek(0)
    b = file.read()

    try:
        return pd.read_excel(io.BytesIO(b), header=None, dtype=str)
    except:
        try:
            return pd.read_csv(io.StringIO(b.decode("utf-8")), header=None, dtype=str, sep=None, engine="python")
        except:
            return pd.read_csv(io.StringIO(b.decode("latin1")), header=None, dtype=str, sep=None, engine="python")

def extrair_nome_banco_por_extenso(df, nome_arquivo):
    texto = " ".join(
        str(x).upper()
        for x in df.head(20).fillna("").values.flatten()
    )

    nome = nome_arquivo.upper()

    if "SICOOB" in texto or "SICOOB" in nome:
        return "SICOOB"

    if "CELCOIN" in texto or "CELCOIN" in nome:
        return "CELCOIN"

    if any(x in texto for x in ["DELFINANCE", "DELBANK", "DEL FINANCE"]):
        return "DELFINANCE"

    return "BANCO_GENERICO"

def localizar_header(df):
    for i, row in df.iterrows():
        vals = [str(x).upper() for x in row.values if pd.notna(x)]

        if any("DATA" in v for v in vals) and any("VALOR" in v for v in vals):
            return i

        if "HISTÓRICO" in vals or "HISTORICO" in vals:
            return i

    return None

def extrair_nome_real(historico, anexas):
    nome = historico

    for linha in anexas:
        txt = str(linha).upper().strip()

        if not txt:
            continue

        if any(x in txt for x in [
            "RECEBIMENTO PIX",
            "PAGAMENTO PIX",
            "SOLICITACAO PIX",
            "CODIGO TED",
            "AUTENTICACAO"
        ]):
            continue

        if re.fullmatch(r'[\d\.\-/ ]+', txt):
            continue

        if "***" in txt:
            continue

        nome = txt
        break

    return nome

def ler_extrato_dinamico(file):

    df = ler_dataframe_upload(file)

    if df.empty:
        return [], "BANCO_GENERICO"

    banco = extrair_nome_banco_por_extenso(df, file.name)

    idx_header = localizar_header(df)

    if idx_header is None:
        return [], banco

    headers = [str(x).upper().strip() for x in df.iloc[idx_header].values]

    pos_data = next((i for i,h in enumerate(headers) if "DATA" in h), 0)

    pos_hist = next(
        (i for i,h in enumerate(headers)
         if any(k in h for k in ["HIST","DESC","FAVOR","NOME","CONTRAPARTE"])),
        min(2, len(headers)-1)
    )

    pos_valor = next(
        (i for i,h in enumerate(headers) if "VALOR" in h),
        len(headers)-1
    )

    dados = list(df.iloc[idx_header+1:].values)

    transacoes = []
    i = 0

    while i < len(dados):

        linha = dados[i]

        dt = converter_data(linha[pos_data])

        if not dt:
            i += 1
            continue

        valor_original = linha[pos_valor]
        valor = limpar_valor(valor_original)

        if valor <= 0:
            i += 1
            continue

        historico = str(linha[pos_hist]).strip()

        anexas = []
        j = i + 1

        while j < len(dados):

            prox = dados[j]

            if converter_data(prox[pos_data]):
                break

            conteudo = " ".join(
                str(x).strip()
                for x in prox
                if pd.notna(x)
            )

            if conteudo:
                anexas.append(conteudo)

            j += 1

        bloco = (historico + " " + " ".join(anexas)).upper()

        nome_real = extrair_nome_real(historico, anexas)

        credito = (
            "RECEB" in bloco or
            "CREDITO" in bloco or
            str(valor_original).upper().endswith("C")
        )

        if (
            "DEBITO" in bloco or
            "PAGAMENTO" in bloco or
            str(valor_original).upper().endswith("D") or
            "-" in str(valor_original)
        ):
            credito = False

        nf = ""
        m = re.search(r'NF\s*([0-9]+)', bloco)
        if m:
            nf = m.group(1)

        transacoes.append({
            "Data": dt.strftime("%d/%m/%Y"),
            "Valor": valor,
            "Razao_Social": nome_real,
            "Is_Credito": credito,
            "Nota_Fiscal_Anexa": nf
        })

        i = j

    return transacoes, banco

# ================= CONTAS =================

def carregar_cadastro_contas(file):

    df = ler_dataframe_upload(file)

    mapa = {}

    for _, r in df.iterrows():

        vals = [str(x).strip() for x in r.values if pd.notna(x)]

        if len(vals) < 2:
            continue

        cod = vals[0].split(".")[0]

        if not cod.isdigit():
            continue

        nome = vals[-1].upper().strip()

        mapa[cod] = {
            "nome_completo": nome,
            "palavras": higienizar_texto_lista_palavras(nome)
        }

    return mapa

def buscar_dados_conta_completos(nome_pesquisa, mapa, conta_receita):

    norm = normalizar_para_match(nome_pesquisa)

    if not norm:
        return "", nome_pesquisa

    if "ERNILDO" in norm:
        return "1136", "ERNILDO OPERACAO DE CRYPTO"

    if any(x in norm for x in ["PIXBET","FLABET","BETDASORTE","SICKBET"]):
        return conta_receita, "PIXBET SOLUCOES TECNOLOGICAS LTDA"

    palavras = set(higienizar_texto_lista_palavras(nome_pesquisa))

    melhor_cod = ""
    melhor_nome = nome_pesquisa
    melhor_score = 0

    for cod, dados in mapa.items():
        score = len(palavras.intersection(set(dados["palavras"])))

        if score > melhor_score:
            melhor_score = score
            melhor_cod = cod
            melhor_nome = dados["nome_completo"]

    if melhor_score >= 2:
        return melhor_cod, melhor_nome

    return "", nome_pesquisa.upper()

# ================= STREAMLIT =================

with st.sidebar:
    st.header("Configurações")
    banco_conta = st.text_input("Conta Banco", "2093")
    conta_receita = st.text_input("Conta Receita", "4101")

st.title("Portal de Conciliação V3")

f_extrato = st.file_uploader("Extrato", type=["xlsx","csv"])
f_contas = st.file_uploader("Plano de Contas", type=["xlsx","csv"])
f_entradas = st.file_uploader("Entradas/Fiscal (opcional)", type=["xlsx","csv"])

if f_extrato and f_contas:

    mapa = carregar_cadastro_contas(f_contas)

    extrato, banco = ler_extrato_dinamico(f_extrato)

    saida = []

    for tx in extrato:

        cod, nome = buscar_dados_conta_completos(
            tx["Razao_Social"],
            mapa,
            conta_receita
        )

        if tx["Is_Credito"]:
            deb = banco_conta
            cred = cod if cod else conta_receita
            hist = f"RECEB {nome}"

        else:
            deb = cod if cod else "CONTA_MANUAL"
            cred = banco_conta
            hist = f"PAGTO {nome}"

        saida.append({
            "Data": tx["Data"],
            "Deb": deb,
            "Cred": cred,
            "Valor_Original": tx["Valor"],
            "Valor": formatar_moeda_br(tx["Valor"]),
            "Historico": hist
        })

    df_final = pd.DataFrame(saida)

    st.success(f"{len(df_final)} lançamentos processados - {banco}")

    st.dataframe(df_final, use_container_width=True)

    excel = io.BytesIO()

    with pd.ExcelWriter(excel, engine="openpyxl") as writer:
        df_final.to_excel(writer, index=False)

    st.download_button(
        "Baixar Excel",
        excel.getvalue(),
        "conciliacao.xlsx"
    )

    txt = io.StringIO()

    for _, r in df_final.iterrows():
        txt.write(
            f"{r['Data']};{r['Deb']};{r['Cred']};"
            f"{formatar_valor_dominio(r['Valor_Original'])};;"
            f"{r['Historico']};;;;\n"
        )

    st.download_button(
        "Baixar TXT Domínio",
        txt.getvalue().encode("utf-8"),
        "conciliacao.txt"
    )
