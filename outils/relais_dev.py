"""Serveur de développement : sert le site en local ET relaie les appels à l'API
Claude en y ajoutant la clé du fichier .env (la clé ne passe jamais par le navigateur).

  python outils/relais_dev.py [port]          puis ouvrir http://localhost:8765

Dans l'application, pour l'utiliser : clé « dev », et dans la console du navigateur
  localStorage.setItem("adresseApi", location.origin)
N'écoute que sur la machine locale (127.0.0.1). Ne sert jamais les fichiers cachés (.env…).
"""
import sys
import urllib.error
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass


def cle_api() -> str:
    for ligne in (RACINE / ".env").read_text(encoding="utf-8").splitlines():
        if ligne.startswith("ANTHROPIC_API_KEY="):
            return ligne.split("=", 1)[1].strip()
    raise SystemExit("ANTHROPIC_API_KEY absente de .env")


CLE = cle_api()
TRANSMIS = ("content-type", "anthropic-version", "anthropic-beta")


class Relais(SimpleHTTPRequestHandler):
    def _interdit(self) -> bool:
        parties = Path(self.path.split("?")[0]).parts
        return any(p.startswith(".") and p not in (".", "..") for p in parties) or "eval" in parties

    def do_GET(self):
        if self.path.startswith("/v1/"):  # lecture d'un lot (API Batch)
            return self._relayer("GET")
        if self._interdit():
            return self.send_error(404)
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self):
        if not self.path.startswith("/v1/"):
            return self.send_error(404)
        self._relayer("POST")

    def _relayer(self, methode: str):
        corps = self.rfile.read(int(self.headers.get("content-length", 0))) if methode == "POST" else None
        entetes = {k: v for k, v in self.headers.items() if k.lower() in TRANSMIS}
        entetes["x-api-key"] = CLE
        req = urllib.request.Request("https://api.anthropic.com" + self.path, corps, entetes, method=methode)
        try:
            with urllib.request.urlopen(req, timeout=600) as rep:
                statut, donnees, type_ = rep.status, rep.read(), rep.headers.get("content-type")
        except urllib.error.HTTPError as e:
            statut, donnees, type_ = e.code, e.read(), e.headers.get("content-type")
        self.send_response(statut)
        self.send_header("content-type", type_ or "application/json")
        self.send_header("content-length", str(len(donnees)))
        self.end_headers()
        self.wfile.write(donnees)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"http://localhost:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), partial(Relais, directory=str(RACINE))).serve_forever()
