#!/usr/bin/env python3

import telnetlib
import time
import subprocess
import ipaddress
import socket
import sys
import os
import re
from datetime import datetime

REDE = "10.0.80.0/28"

LOG_OK = "login_ok.log"
LOG_FAIL = "login_fail.log"
LOG_CFG = "coleta_modelos.log"
CSV_INVENTARIO = "inventario_modelos.csv"
DIR_OUTPUT = "outputs_modelos"
DIR_LISTAS = "listas_por_modelo"

CREDENCIAIS = [
    ("admin", "admin"),
    ("dmview", "dmview@toledo"),
    ("jean", "portugal@1985"),
    ("jean", "Portugal@1985"),
]

# ordem de tentativa
COMANDOS_IDENTIFICACAO = [
    "show sysinfo",
    "show system",
    "show version",
    "show switch",
    "show inventory",
]


def log(arquivo, msg):
    with open(arquivo, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now()} - {msg}\n")


def garantir_diretorios():
    os.makedirs(DIR_OUTPUT, exist_ok=True)
    os.makedirs(DIR_LISTAS, exist_ok=True)


def gerar_ips(rede):
    try:
        return [str(ip) for ip in ipaddress.ip_network(rede).hosts()]
    except Exception as e:
        print(f"Erro ao gerar IPs: {e}")
        sys.exit(1)


def ping(ip):
    try:
        r = subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return r.returncode == 0
    except Exception:
        return False


def porta_aberta(ip, porta, timeout=2):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((ip, porta))
        s.close()
        return True
    except Exception:
        return False


def read_available(tn, wait=1):
    time.sleep(wait)
    try:
        return tn.read_very_eager().decode(errors="ignore")
    except Exception:
        return ""


def login_telnet(ip, user, password):
    try:
        tn = telnetlib.Telnet(ip, timeout=8)

        banner = read_available(tn, 2)

        if "User:" not in banner and "user:" not in banner and "Username:" not in banner and "login:" not in banner:
            idx, match, text = tn.expect(
                [b"User:", b"user:", b"Username:", b"username:", b"login:"],
                timeout=5
            )
            if idx == -1:
                tn.close()
                return None, "sem prompt de usuario"

        tn.write(user.encode() + b"\n")

        idx, match, text = tn.expect([b"Password:", b"password:"], timeout=5)
        if idx == -1:
            tn.close()
            return None, "sem prompt de senha"

        tn.write(password.encode() + b"\n")
        time.sleep(2)

        retorno = read_available(tn, 2)

        if "User:" in retorno or "user:" in retorno or "Username:" in retorno or "login:" in retorno:
            tn.close()
            return None, "voltou para prompt de usuario"

        tn.write(b"\n")
        prompt = read_available(tn, 1)
        retorno_total = retorno + prompt

        if "User:" in retorno_total or "user:" in retorno_total or "Username:" in retorno_total or "login:" in retorno_total:
            tn.close()
            return None, "voltou para prompt de usuario apos enter"

        if "#" in retorno_total:
            return tn, "#"

        if ">" in retorno_total:
            return tn, ">"

        tn.close()
        return None, f"prompt nao identificado: {repr(retorno_total)}"

    except Exception as e:
        return None, str(e)


def enter_enable(tn):
    tn.write(b"enable\n")
    saida = read_available(tn, 1)

    for _ in range(5):
        parte = read_available(tn, 1)
        if parte:
            saida += parte
        if "#" in saida:
            return True, saida
        if "User:" in saida or "Password:" in saida:
            return False, saida

    return False, saida


def run_command(tn, comando, loops=8, espera=1):
    tn.write((comando + "\n").encode())
    output = ""

    for _ in range(loops):
        parte = read_available(tn, espera)
        if parte:
            output += parte

    return output


def comando_invalido(saida):
    erros = [
        "invalid input",
        "invalid input detected",
        "% invalid",
        "unknown command",
        "unrecognized command",
        "incomplete command",
        "error:",
    ]
    s = saida.lower()
    return any(e in s for e in erros)


def extrair_hostname(saida):
    padroes = [
        r"System Name\.{2,}\s*(.+)",
        r"System Name\s*:\s*(.+)",
        r"hostname\s*[:=]\s*(.+)",
    ]

    for p in padroes:
        m = re.search(p, saida, re.IGNORECASE)
        if m:
            return m.group(1).strip()

    # tenta pelo prompt
    m = re.search(r"\(([^)]+)\)\s*[>#]", saida)
    if m:
        return m.group(1).strip()

    m = re.search(r"\n([A-Za-z0-9._-]+)[>#]\s*$", saida)
    if m:
        return m.group(1).strip()

    return "DESCONHECIDO"


def extrair_modelo(saida):
    padroes = [
        r"System Description\.{2,}\s*(.+)",
        r"System Description\s*:\s*(.+)",
        r"Model(?: Number)?\s*[:.]+\s*(.+)",
        r"Product Name\s*[:.]+\s*(.+)",
        r"Chassis\s*[:.]+\s*(.+)",
    ]

    for p in padroes:
        m = re.search(p, saida, re.IGNORECASE)
        if m:
            return m.group(1).strip()

    # fallback por palavras comuns
    for linha in saida.splitlines():
        l = linha.strip()
        if any(x in l for x in ["DM1200", "DM", "Standalone", "Switch", "Router"]):
            return l

    return "MODELO_NAO_IDENTIFICADO"


def normalizar_nome_arquivo(texto):
    texto = texto.strip().replace(" ", "_")
    texto = re.sub(r"[^A-Za-z0-9._-]", "_", texto)
    texto = re.sub(r"_+", "_", texto)
    return texto[:120]


def salvar_output_ip(ip, conteudo):
    caminho = os.path.join(DIR_OUTPUT, f"{ip}.txt")
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(conteudo)


def salvar_csv_linha(ip, hostname, modelo, credencial, prompt, comando_ok):
    novo = not os.path.exists(CSV_INVENTARIO)
    with open(CSV_INVENTARIO, "a", encoding="utf-8") as f:
        if novo:
            f.write("ip,hostname,modelo,usuario,prompt,comando_ok\n")
        f.write(f'"{ip}","{hostname}","{modelo}","{credencial}","{prompt}","{comando_ok}"\n')


def salvar_lista_por_modelo(modelo, ip):
    nome = normalizar_nome_arquivo(modelo)
    caminho = os.path.join(DIR_LISTAS, f"{nome}.txt")
    with open(caminho, "a", encoding="utf-8") as f:
        f.write(ip + "\n")


def identificar_modelo(tn):
    resultados = []

    for cmd in COMANDOS_IDENTIFICACAO:
        saida = run_command(tn, cmd, loops=6, espera=1)
        resultados.append((cmd, saida))

        if "User:" in saida or "user:" in saida:
            return None, None, "\n".join(
                [f"\n##### COMANDO: {c} #####\n{s}" for c, s in resultados]
            )

        if not comando_invalido(saida) and saida.strip():
            hostname = extrair_hostname(saida)
            modelo = extrair_modelo(saida)
            bruto = "\n".join(
                [f"\n##### COMANDO: {c} #####\n{s}" for c, s in resultados]
            )
            return cmd, (hostname, modelo), bruto

    bruto = "\n".join([f"\n##### COMANDO: {c} #####\n{s}" for c, s in resultados])
    return None, ("DESCONHECIDO", "MODELO_NAO_IDENTIFICADO"), bruto


def processar_ip(ip):
    print(f"\n🔍 Testando IP: {ip}")

    if not ping(ip):
        print(f"🚫 SEM PING - {ip}")
        return

    print(f"📡 PING OK - {ip}")

    if not porta_aberta(ip, 23):
        print(f"❌ TELNET FECHADO - {ip}")
        log(LOG_FAIL, f"{ip} - porta 23 fechada")
        return

    autenticado = False

    for user, password in CREDENCIAIS:
        print(f"   ↳ Testando credencial: {user} / {password}")

        tn, estado = login_telnet(ip, user, password)

        if tn is None:
            print(f"   ❌ Falhou: {user} / {password} - {estado}")
            continue

        print(f"✅ LOGIN OK - {ip} - TELNET - {user} - prompt {estado}")
        log(LOG_OK, f"{ip} - TELNET - {user} - prompt {estado}")

        output_total = ""

        if estado == ">":
            ok, saida_enable = enter_enable(tn)
            output_total += "\n##### ENABLE #####\n" + saida_enable

            if not ok:
                print(f"❌ ENABLE FAIL - {ip}")
                log(LOG_FAIL, f"{ip} - enable fail com {user}")
                tn.close()
                continue

        comando_ok, info, bruto = identificar_modelo(tn)
        output_total += bruto

        if "User:" in output_total or "user:" in output_total:
            print(f"❌ Sessão inválida após comando - {ip}")
            log(LOG_FAIL, f"{ip} - sessao voltou para User: com {user}")
            tn.close()
            continue

        if info is None:
            print(f"❌ Não foi possível identificar o modelo - {ip}")
            log(LOG_FAIL, f"{ip} - sem identificacao de modelo com {user}")
            salvar_output_ip(ip, output_total)
            tn.close()
            continue

        hostname, modelo = info

        print(f"📦 MODELO - {ip} -> {modelo}")
        print(f"📝 HOSTNAME - {ip} -> {hostname}")
        print(f"🧪 COMANDO OK - {ip} -> {comando_ok}")

        log(LOG_CFG, f"{ip} - hostname={hostname} - modelo={modelo} - comando={comando_ok}")
        salvar_output_ip(ip, output_total)
        salvar_csv_linha(ip, hostname, modelo, user, estado, comando_ok)
        salvar_lista_por_modelo(modelo, ip)

        tn.close()
        autenticado = True
        break

    if not autenticado:
        print(f"❌ LOGIN FAIL - {ip}")
        log(LOG_FAIL, f"{ip} - nenhuma credencial funcionou")


def main():
    garantir_diretorios()
    ips = gerar_ips(REDE)

    for ip in ips:
        processar_ip(ip)


if __name__ == "__main__":
    main()
