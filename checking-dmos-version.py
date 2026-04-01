#!/usr/bin/env python3

import paramiko
import telnetlib
import time
import sys
import subprocess
import ipaddress
import socket
from datetime import datetime

REDE = "10.0.80.0/24"

LOG_OK = "login_ok.log"
LOG_FAIL = "login_fail.log"
LOG_CFG = "config_aplicada.log"

CREDENCIAIS = [
    ("admin", "admin"),
    ("dmview", "dmview@toledo"),
    ("jean", "portugal@1985"),
    ("jean", "Portugal@1985"),
]

# Comando correto para esses switches
COMANDO_SHOW = "show sysinfo"

# ---------- UTIL ----------
def log(arquivo, msg):
    with open(arquivo, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now()} - {msg}\n")

def gerar_ips(rede):
    try:
        return [str(ip) for ip in ipaddress.ip_network(rede).hosts()]
    except Exception as e:
        print(f"❌ Erro ao gerar IPs da rede {rede}: {e}")
        sys.exit(1)

def ping(ip):
    try:
        resultado = subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return resultado.returncode == 0
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

# ---------- SSH ----------
def conectar_ssh(ip, user, password):
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(
            hostname=ip,
            username=user,
            password=password,
            timeout=5,
            banner_timeout=5,
            auth_timeout=5,
            allow_agent=False,
            look_for_keys=False
        )
        return ssh
    except Exception:
        return None

def ler_saida_ssh(shell, tentativas=5, espera=1):
    output = ""
    sem_saida = 0

    while sem_saida < tentativas:
        time.sleep(espera)
        if shell.recv_ready():
            output += shell.recv(65535).decode(errors="ignore")
            sem_saida = 0
        else:
            sem_saida += 1

    return output

def aplicar_comando_ssh(ssh, ip):
    try:
        shell = ssh.invoke_shell()
        time.sleep(2)

        banner = ""
        while shell.recv_ready():
            banner += shell.recv(65535).decode(errors="ignore")

        # tenta enable caso prompt venha com >
        if ">" in banner and "#" not in banner:
            shell.send("enable\n")
            time.sleep(1)
            banner += ler_saida_ssh(shell, tentativas=2, espera=1)

        shell.send(COMANDO_SHOW + "\n")
        output = ler_saida_ssh(shell, tentativas=5, espera=1)

        saida_final = f"{banner}\n##### COMANDO: {COMANDO_SHOW} #####\n{output}"

        print(f"\n📡 RESULTADO SSH - {ip}\n{saida_final}")
        log(LOG_CFG, f"{ip} - SSH OUTPUT:\n{saida_final}")

    except Exception as e:
        print(f"❌ Erro ao executar comando SSH em {ip}: {e}")
        log(LOG_FAIL, f"{ip} - ERRO CMD SSH: {e}")

# ---------- TELNET ----------
def ler_ate_prompt(tn, timeout=5):
    idx, match, text = tn.expect(
        [
            b"User:",
            b"user:",
            b"Username:",
            b"username:",
            b"login:",
            b"Password:",
            b"password:",
            b">",
            b"#",
        ],
        timeout=timeout
    )
    return idx, text.decode(errors="ignore")

def conectar_telnet(ip, user, password):
    try:
        tn = telnetlib.Telnet(ip, timeout=8)

        # espera prompt de user
        idx, saida = ler_ate_prompt(tn, timeout=5)
        if idx not in [0, 1, 2, 3, 4]:
            tn.close()
            return None, None

        tn.write(user.encode() + b"\n")

        # espera password
        idx, saida = ler_ate_prompt(tn, timeout=5)
        if idx not in [5, 6]:
            tn.close()
            return None, None

        tn.write(password.encode() + b"\n")
        time.sleep(2)

        # lê retorno do login
        bruto = tn.read_very_eager().decode(errors="ignore")

        # falha: voltou para user/login
        if "User:" in bruto or "user:" in bruto or "Username:" in bruto or "login:" in bruto:
            tn.close()
            return None, None

        # tenta pegar prompt real
        tn.write(b"\n")
        time.sleep(1)
        prompt_saida = tn.read_very_eager().decode(errors="ignore")
        bruto += prompt_saida

        # falha: voltou para login prompt
        if "User:" in prompt_saida or "user:" in prompt_saida or "Username:" in prompt_saida or "login:" in prompt_saida:
            tn.close()
            return None, None

        # sucesso direto em modo enable
        if "#" in prompt_saida:
            return tn, "#"

        # sucesso em modo user
        if ">" in prompt_saida:
            return tn, ">"

        # fallback: tenta detectar no bruto também
        if "#" in bruto:
            return tn, "#"

        if ">" in bruto:
            return tn, ">"

        tn.close()
        return None, None

    except Exception:
        return None, None

def entrar_enable_telnet(tn):
    try:
        tn.write(b"enable\n")
        time.sleep(1)

        output = ""
        tentativas = 0

        while tentativas < 5:
            time.sleep(1)
            parcial = tn.read_very_eager().decode(errors="ignore")
            if parcial:
                output += parcial
                if "#" in parcial:
                    return True, output
                if "User:" in parcial or "Password:" in parcial:
                    return False, output
                tentativas = 0
            else:
                tentativas += 1

        return False, output
    except Exception:
        return False, ""

def aplicar_comando_telnet(tn, ip, prompt_inicial):
    try:
        output_total = ""

        if prompt_inicial == ">":
            ok_enable, saida_enable = entrar_enable_telnet(tn)
            output_total += "\n##### ENABLE #####\n" + saida_enable

            if not ok_enable:
                print(f"❌ Não foi possível entrar em enable no IP {ip}")
                log(LOG_FAIL, f"{ip} - nao entrou em enable")
                tn.close()
                return

        tn.write((COMANDO_SHOW + "\n").encode())
        time.sleep(2)

        output_cmd = ""
        tentativas_sem_saida = 0

        while tentativas_sem_saida < 5:
            time.sleep(1)
            parcial = tn.read_very_eager().decode(errors="ignore")
            if parcial:
                output_cmd += parcial
                tentativas_sem_saida = 0
            else:
                tentativas_sem_saida += 1

        output_total += f"\n##### COMANDO: {COMANDO_SHOW} #####\n{output_cmd}"

        print(f"\n📡 RESULTADO TELNET - {ip}\n{output_total}")
        log(LOG_CFG, f"{ip} - TELNET OUTPUT:\n{output_total}")
        tn.close()

    except Exception as e:
        print(f"❌ Erro ao executar comando TELNET em {ip}: {e}")
        log(LOG_FAIL, f"{ip} - ERRO CMD TELNET: {e}")

# ---------- MAIN ----------
def main():
    ips = gerar_ips(REDE)

    for ip in ips:
        print(f"\n🔍 Testando IP: {ip}")

        if not ping(ip):
            print(f"🚫 SEM PING - {ip}")
            continue

        print(f"📡 PING OK - {ip}")
        autenticado = False

        for user, password in CREDENCIAIS:
            print(f"   ↳ Testando credencial: {user} / {password}")

            # tenta SSH primeiro
            if porta_aberta(ip, 22):
                ssh = conectar_ssh(ip, user, password)
                if ssh is not None:
                    print(f"✅ LOGIN OK - {ip} - SSH - {user}")
                    log(LOG_OK, f"{ip} - SSH - {user}")
                    aplicar_comando_ssh(ssh, ip)
                    ssh.close()
                    autenticado = True
                    break

            # tenta TELNET
            if porta_aberta(ip, 23):
                tn, prompt = conectar_telnet(ip, user, password)
                if tn is not None:
                    print(f"✅ LOGIN OK - {ip} - TELNET - {user} - prompt {prompt}")
                    log(LOG_OK, f"{ip} - TELNET - {user} - prompt {prompt}")
                    aplicar_comando_telnet(tn, ip, prompt)
                    autenticado = True
                    break

            print(f"   ❌ Falhou: {user} / {password}")

        if not autenticado:
            print(f"❌ LOGIN FAIL - {ip}")
            log(LOG_FAIL, f"{ip} - nenhuma credencial funcionou")

if __name__ == "__main__":
    main()
