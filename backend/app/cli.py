"""Commandes d'administration : `greenard create-admin`, `greenard prepare-invariants`."""

import argparse
import getpass
import sys

from sqlalchemy import select

from app.core.security import MIN_PASSWORD_LENGTH, hash_password
from app.db.models import User
from app.db.session import SessionLocal


def create_admin(email: str, full_name: str, password: str | None) -> None:
    password = password or getpass.getpass("Mot de passe / Password: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        sys.exit(f"Mot de passe trop court (minimum {MIN_PASSWORD_LENGTH} caractères)")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email.lower()))
        if user is None:
            user = User(email=email.lower(), full_name=full_name, password_hash=hash_password(password), is_admin=True)
            db.add(user)
        else:
            user.password_hash = hash_password(password)
            user.is_admin = True
            user.is_active = True
        db.commit()
    print(f"Administrateur prêt : {email}")


def prepare_invariants(models: list[str]) -> None:
    from app.nwp.invariants.fetch import prepare

    for m in models:
        meta = prepare(m, lambda p, msg, m=m: print(f"  [{m}] {p:4.0%} {msg}"))
        print(f"{m}: {meta}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="greenard")
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin", help="Créer ou réinitialiser un administrateur")
    a.add_argument("--email", required=True)
    a.add_argument("--name", default="")
    a.add_argument("--password", help="(déconseillé : préférer la saisie interactive)")
    p = sub.add_parser("prepare-invariants", help="Télécharger les champs invariants des modèles")
    p.add_argument("models", nargs="*", default=["gfs", "ifs", "icon", "icon_eu"])
    args = parser.parse_args()
    if args.cmd == "create-admin":
        create_admin(args.email, args.name, args.password)
    elif args.cmd == "prepare-invariants":
        prepare_invariants(args.models)


if __name__ == "__main__":
    main()
