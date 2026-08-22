# tests/machiavelli/game/test_tables.py

from machiavelli.game.tables import GameTables


def test_matrix_dimensions():
    """Verifica las dimensiones las matrices de desastre (hambruna y peste)."""
    assert len(GameTables.famine) == 11
    assert all(len(row) == 11 for row in GameTables.famine)

    assert len(GameTables.plague) == 11
    assert all(len(row) == 11 for row in GameTables.plague)


def test_expenses_dictionary_structure():
    """Verifica la estructura interna de los diccionarios de gastos (expenses)."""
    assert "A" in GameTables.expenses
    sample = GameTables.expenses["A"]
    assert sample["text"] == "Paliar hambruna"
    assert sample["target_type"] == "province"
    assert sample["cost"] == 3

    # Validar que todas las entradas cumplan el contrato de TypedDict ExpenseInfo
    for code, info in GameTables.expenses.items():
        assert isinstance(code, str)
        assert "text" in info
        assert info["target_type"] in ("province", "power", "unit")
        assert isinstance(info["cost"], int)


def test_orders_dictionary_structure():
    """Verifica la estructura de las órdenes militares y de mantenimiento."""
    # Órdenes militares
    assert "A" in GameTables.military_orders
    assert GameTables.military_orders["A"]["text"] == "Avanzar a Provincia o Mar"
    assert GameTables.military_orders["A"]["target_type"] == "location"

    for info in GameTables.military_orders.values():
        assert "text" in info
        assert "target_type" in info

    # Órdenes de mantenimiento
    assert "M" in GameTables.maintenance_orders
    assert GameTables.maintenance_orders["M"]["target_type"] is None


def test_province_code_formatting():
    """Garantiza el formato de los códigos de provincia."""
    famine_codes = {cell for row in GameTables.famine for cell in row if cell}
    plague_codes = {cell for row in GameTables.plague for cell in row if cell}

    for code in famine_codes | plague_codes:
        assert code.islower(), (
            f"El código '{code}' debe estar completamente en minúsculas."
        )
        assert " " not in code, f"El código '{code}' no debe contener espacios."
        assert len(code) <= 5, f"El código '{code}' debe ser menor de 5 caracteres."


def test_table_lengths_and_counts():
    """Verifica las dimensiones de los diccionarios y listas de datos."""
    assert len(GameTables.powers) == 9
    assert len(GameTables.actors) == 4
    assert len(GameTables.expenses) == 11
    assert len(GameTables.military_orders) == 7
    assert len(GameTables.maintenance_orders) == 3
    assert len(GameTables.disasters) == 6
    assert len(GameTables.seasons) == 4
    assert len(GameTables.assassination_rebellions) == 4
