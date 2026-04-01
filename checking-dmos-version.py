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

COMANDOS_CONFIG = [
    "show system",
]

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

def porta_aberta(ip, porta, timeout=3):
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
            timeout=6,
            banner_timeout=6,
            auth_timeout=6,
            allow_agent=False,
            look_for_keys=False
        )
        return ssh
    except Exception:
        return None

def aplicar_comandos_ssh(ssh, ip):
    try:
        shell = ssh.invoke_shell()
        time.sleep(2)

        output_total = ""

        while shell.recv_ready():
            output_total += shell.recv(65535).decode(errors="ignore")

        for cmd in COMANDOS_CONFIG:
            shell.send(cmd + "\n")
            time.sleep(2)

            output = ""
            tentativas_sem_saida = 0

            while tentativas_sem_saida < 5:
                time.sleep(1)
                if shell.recv_ready():
                    output += shell.recv(65535).decode(errors="ignore")
                    tentativas_sem_saida = 0
                else:
                    tentativas_sem_saida += 1

            output_total += f"\n##### COMANDO: {cmd} #####\n{output}"

        print(f"\n📡 RESULTADO SSH - {ip}\n{output_total}")
        log(LOG_CFG, f"{ip} - SSH OUTPUT:\n{output_total}")

    except Exception as e:
        print(f"❌ Erro ao executar comando SSH em {ip}: {e}")
        log(LOG_FAIL, f"{ip} - ERRO CMD SSH: {e}")

# ---------- TELNET ----------
def conectar_telnet(ip, user, password):
    try:
        tn = telnetlib.Telnet(ip, timeout=5)

        # aceita vários prompts possíveis de usuário
        idx, match, text = tn.expect(
            [b"User:", b"Username:", b"login:", b"user:", b"username:"],
            timeout=5
        )
        if idx == -1:
            tn.close()
            return None

        tn.write(user.encode() + b"\n")

        idx, match, text = tn.expect(
            [b"Password:", b"password:"],
            timeout=5
        )
        if idx == -1:
            tn.close()
            return None

        tn.write(password.encode() + b"\n")
        time.sleep(2)

        saida = tn.read_very_eager().decode(errors="ignore")

        erros = [
            "invalid",
            "failed",
            "incorrect",
            "login invalid",
            "authentication failed",
            "access denied",
            "bad password",
            "denied",
            "error"
        ]

        if any(erro in saida.lower() for erro in erros):
            tn.close()
            return None

        # força nova leitura do prompt
        tn.write(b"\n")
        time.sleep(1)
        saida2 = tn.read_very_eager().decode(errors="ignore")

        prompts_validos = ["#", ">", "$", ") >", ")#", "]#", "] >"]

        if any(p in saida2 for p in prompts_validos):
            return tn

        # fallback: tenta rodar comando pra validar login
        tn.write(b"show system\n")
        time.sleep(3)
        saida3 = tn.read_very_eager().decode(errors="ignore")

        if any(erro in saida3.lower() for erro in erros):
            tn.close()
            return None

        if saida3.strip():
            return tn

        tn.close()
        return None

    except Exception:
        return None

def aplicar_comandos_telnet(tn, ip):
    try:
        output_total = ""

        for cmd in COMANDOS_CONFIG:
            tn.write(cmd.encode() + b"\n")
            time.sleep(2)

            output = ""
            tentativas_sem_saida = 0

            while tentativas_sem_saida < 5:
                time.sleep(1)
                parcial = tn.read_very_eager().decode(errors="ignore")
                if parcial:
                    output += parcial
                    tentativas_sem_saida = 0
                else:
                    tentativas_sem_saida += 1

            output_total += f"\n##### COMANDO: {cmd} #####\n{output}"

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

            # tenta SSH primeiro se porta 22 aberta
            if porta_aberta(ip, 22, timeout=2):
                ssh = conectar_ssh(ip, user, password)
                if ssh is not None:
                    print(f"✅ LOGIN OK - {ip} - SSH - {user}")
                    log(LOG_OK, f"{ip} - SSH - {user}")
                    aplicar_comandos_ssh(ssh, ip)
                    ssh.close()
                    autenticado = True
                    break

            # tenta TELNET se porta 23 aberta
            if porta_aberta(ip, 23, timeout=2):
                tn = conectar_telnet(ip, user, password)
                if tn is not None:
                    print(f"✅ LOGIN OK - {ip} - TELNET - {user}")
                    log(LOG_OK, f"{ip} - TELNET - {user}")
                    aplicar_comandos_telnet(tn, ip)
                    autenticado = True
                    break

            print(f"   ❌ Falhou: {user} / {password}")

        if not autenticado:
            print(f"❌ LOGIN FAIL - {ip}")
            log(LOG_FAIL, f"{ip} - nenhuma credencial funcionou")

if __name__ == "__main__":
    main()
