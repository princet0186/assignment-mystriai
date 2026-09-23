from .storage import invoice_by_key


def find_invoice(db, payment):
    """Return the id of the invoice a payment belongs to, or None.

    A payment attaches only to an invoice with both the same customer_id and
    invoice_number (BUSINESS_RULES.md, "Records and identity"). The amount is
    deliberately not consulted: two customers may owe identical amounts, so an
    amount alone does not establish identity.
    """
    exact = invoice_by_key(db, payment['customer_id'], payment['invoice_number'])
    return exact['id'] if exact else None
