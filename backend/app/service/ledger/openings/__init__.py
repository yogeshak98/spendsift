"""Opening balances: which accounts have one, the first-import prompt, and
the approved writes (add, correct, mark zero, mutual-fund opening lots).

Every journal write for an opening goes through `OpeningService`, which
delegates each method to one module so the modules can change independently.
"""
