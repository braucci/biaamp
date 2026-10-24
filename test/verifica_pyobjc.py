"""verifica_pyobjc.py — Controllo statico delle regole PyObjC su app.py.

Uso:   python3 test/verifica_pyobjc.py [app.py]


Per ogni classe che eredita da una classe Objective-C:
  • metodo con @objc.python_method → libero;
  • altrimenti è un selettore: il numero di '_' deve essere uguale al
    numero di argomenti (self escluso), non può iniziare con '_' e non può
    avere '_' interni consecutivi (camelCase).
Inoltre: ogni selettore passato come stringa ("nome:") a menu e timer
deve corrispondere a un metodo esistente nella classe controllore.
"""
import ast
import re
import sys

from pathlib import Path
percorso = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent.parent / "app.py")
sorgente = open(percorso, encoding="utf-8").read()
albero = ast.parse(sorgente)
errori, metodi_per_classe = [], {}


def decorato(f):
    for d in f.decorator_list:
        if isinstance(d, ast.Attribute) and d.attr == "python_method":
            return True
    return False


for nodo in albero.body:
    if not isinstance(nodo, ast.ClassDef):
        continue
    basi = [ast.unparse(b) for b in nodo.bases]
    objc_cls = any(b.startswith(("AppKit.", "Foundation.")) or b in ("VistaBase",) for b in basi)
    if not objc_cls:
        continue
    metodi_per_classe[nodo.name] = set()
    for f in nodo.body:
        if not isinstance(f, ast.FunctionDef):
            continue
        metodi_per_classe[nodo.name].add(f.name)
        if decorato(f):
            continue
        nargs = len(f.args.args) - 1
        if f.name.startswith("_"):
            errori.append(f"{nodo.name}.{f.name}: metodo privato senza @objc.python_method")
        elif f.name.count("_") != nargs:
            errori.append(f"{nodo.name}.{f.name}: {f.name.count('_')} '_' ma {nargs} argomenti")
        elif "__" in f.name:
            errori.append(f"{nodo.name}.{f.name}: '_' consecutivi")

ctrl = metodi_per_classe.get("ControlloreApp", set())
selettori_ctrl = {m.replace("_", ":") for m in ctrl}
standard = {"hide:", "hideOtherApplications:", "unhideAllApplications:", "terminate:", "undo:", "redo:",
            "cut:", "copy:", "paste:", "selectAll:",
            # selettori di sistema confrontati (non azioni da definire)
            "moveDown:", "cancelOperation:"}
for s in sorted(set(re.findall(r'"([A-Za-z]+:)"', sorgente))):
    if s.startswith(("set", "init")) or s in standard:
        continue
    if s not in selettori_ctrl:
        errori.append(f"selettore {s!r} usato ma non definito in ControlloreApp")

# Ogni risorsa caricata da app.py con _risorsa("…") deve essere dichiarata in
# setup.py, altrimenti py2app non la copia nel pacchetto e l'app non parte.
cartella = Path(percorso).resolve().parent
setup = (cartella / "setup.py").read_text(encoding="utf-8") if (cartella / "setup.py").exists() else ""
for nome in sorted(set(re.findall(r'_risorsa\(\s*"([^"]+)"', sorgente)) | set(re.findall(r'\("([a-z_]+\.js)",', sorgente))):
    if f"risorse/{nome}" not in setup:
        errori.append(f"risorsa {nome!r} usata da app.py ma assente da setup.py")
    elif not (cartella / "risorse" / nome).exists():
        errori.append(f"risorsa {nome!r} dichiarata ma il file non esiste")

n = sum(len(v) for v in metodi_per_classe.values())
if errori:
    print("\n".join("✗ " + e for e in errori))
    sys.exit(1)
print(f"✓ {len(metodi_per_classe)} classi Objective-C, {n} metodi: regole PyObjC rispettate; "
      "selettori dei menu tutti definiti; risorse dichiarate in setup.py")

# © 2026 B. Raucci — Licenza MIT
