"""Research inventory decisions; this package owns no stock ledger or orders."""
from .core import InventoryError, recommend, evaluate_events, compare_policies

__all__ = ['InventoryError', 'recommend', 'evaluate_events', 'compare_policies']
