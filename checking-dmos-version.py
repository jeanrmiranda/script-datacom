#!/usr/bin/env python3

import paramiko
import telnetlib
import time
import sys
import subprocess
import ipaddress
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

def aplicar_config_ssh(ssh, ip):
    try:
        shell = ssh.invoke_shell()
        time.sleep(2)

        output_total = ""
        if shell.recv_ready():
            output_total += shell.recv(65535).decode(errors="ignore")

        for cmd in COMANDOS_CONFIG:
            shell.send(cmd + "\n")
            time.sleep(2)

            output = ""
            tentativas = 0

            while tentativas < 5:
                time.sleep(1)
                if shell.recv_ready():
                    output += shell.recv(65535).decode(errors="ignore")
                    tentativas = 0
                else:
                    tentativas += 1

            output_total += f"\n##### COMANDO: {cmd} #####\n"
            output_total += output

        print(f"\n📡 RESULTADO - {ip}\n{output_total}")
        log(LOG_CFG, f"{ip} - OUTPUT:\n{output_total}")

    except Exception as e:
        print(f"❌ Erro ao executar comando no IP {ip}: {e}")
        log(LOG_FAIL, f"{ip} - ERRO EXEC CMD SSH: {e}")

# ---------- TELNET ----------
def testar_telnet(ip, user, password):
    try:
        tn = telnetlib.Telnet(ip, timeout=5)
        tn.read_until(b"Username:", timeout=3)
        tn.write(user.encode() + b"\n")
        tn.read_until(b"Password:", timeout=3)
        tn.write(password.encode() + b"\n")
        time.sleep(2)

        saida = tn.read_very_eager().decode(errors="ignore")

        if "invalid" in saida.lower() or "failed" in saida.lower():
            tn.close()
            return False

        return tn
    except Exception:
        return None

def aplicar_comando_telnet(tn, ip):
    try:
        output_total = ""

        for cmd in COMANDOS_CONFIG:
            tn.write(cmd.encode() + b"\n")
            time.sleep(2)

            output = tn.read_very_eager().decode(errors="ignore")
            output_total += f"\n##### COMANDO: {cmd} #####\n"
            output_total += output

        print(f"\n📡 RESULTADO TELNET - {ip}\n{output_total}")
        log(LOG_CFG, f"{ip} - OUTPUT TELNET:\n{output_total}")

        tn.close()

    except Exception as e:
        print(f"❌ Erro ao executar comando via TELNET no IP {ip}: {e}")
        log(LOG_FAIL, f"{ip} - ERRO EXEC CMD TELNET: {e}")

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
            ssh = conectar_ssh(ip, user, password)
            if ssh:
                print(f"✅ LOGIN OK - {ip} - SSH - {user}")
                log(LOG_OK, f"{ip} - SSH - {user}")
                aplicar_config_ssh(ssh, ip)
                ssh.close()
                autenticado = True
                break

            tn = testar_telnet(ip, user, password)
            if tn:
                print(f"⚠️ LOGIN OK - {ip} - TELNET - {user}")
                log(LOG_OK, f"{ip} - TELNET - {user}")
                aplicar_comando_telnet(tn, ip)
                autenticado = True
                break

        if not autenticado:
            print(f"❌ LOGIN FAIL - {ip}")
            log(LOG_FAIL, ip)

if __name__ == "__main__":
    main()
