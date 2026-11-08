from backend.app.service.categorize.dictionary import MerchantDictionary


def test_lookup_known_merchant_substring():
    d = MerchantDictionary({"SWIGGY": "Expenses:Food"})
    assert d.lookup("SWIGGY") == "Expenses:Food"
    assert d.lookup("SWIGGY BANGALORE") == "Expenses:Food"  # substring match


def test_lookup_unknown_returns_none():
    assert (
        MerchantDictionary({"SWIGGY": "Expenses:Food"}).lookup("RANDOM VENDOR") is None
    )


def test_loads_shipped_seed():
    d = MerchantDictionary.from_seed()
    assert d.lookup("AMAZON INDIA") == "Expenses:Shopping"
    assert d.lookup("NETFLIX") == "Expenses:Entertainment"
