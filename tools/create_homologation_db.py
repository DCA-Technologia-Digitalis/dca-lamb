"""Create one synthetic SQLite source for isolated migration rehearsals."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("Uso: python tools/create_homologation_db.py CAMINHO_NOVO.sqlite3")
        return 2
    destination = Path(sys.argv[1]).resolve()
    if destination.exists():
        raise FileExistsError("O destino ja existe; escolha um novo arquivo para preservar dados.")

    os.environ["DV_DATABASE_PATH"] = str(destination)
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("POSTGRES_URL", None)
    for variable in ("ADMIN_SENHA", "ADMIN_PIN", "DCA_SUPER_ADMIN_USER",
                     "DCA_SUPER_ADMIN_NAME", "DCA_SUPER_ADMIN_PASSWORD"):
        os.environ.pop(variable, None)

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import database  # noqa: PLC0415
    import dv_saude  # noqa: PLC0415
    import commercial  # noqa: PLC0415

    destination.parent.mkdir(parents=True, exist_ok=True)
    database.criar_tabelas()
    dv_saude._ensure_schema()
    commercial.ensure_schema()
    owner_id = commercial.register_customer({
        "organization_name": "Clinica Sintetica de Homologacao",
        "contact_name": "Responsavel Ficticio",
        "email": "owner@example.invalid",
        "account_type": "ESTABLISHMENT",
        "primary_specialty": "Odontologia",
        "specialties": "Medicina",
        "professional_count": "2",
        "unit_count": "1",
        "password": "senha-ficticia-para-teste-12",
        "confirm_data": "yes",
    })
    owner = database.buscar_usuario_por_id(owner_id)
    conn = database.conectar()
    try:
        conn.execute(
            "INSERT INTO dv_unidades(cnes, nome, organization_id) VALUES (?, ?, ?)",
            ("7770001", "Unidade Sintetica", owner["organization_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    commercial.register_customer_user(
        owner["organization_id"], "Profissional Ficticio", "staff@example.invalid",
        "DENTISTA", "7770001",
    )
    patient_id = database.adicionar_paciente(
        "Paciente Sintetico Sem Dados Reais", cnes_unidade="7770001"
    )
    database.adicionar_arquivo(
        patient_id, "Exame", "arquivo-ficticio.pdf", "homologacao-inexistente.pdf",
        "application/pdf", "registro sintentico", cnes_unidade="7770001",
        sha256="a" * 64,
    )
    print("Banco SQLite sintetico criado; nenhum dado real foi usado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
