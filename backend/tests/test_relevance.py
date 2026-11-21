import pytest

from easydial.providers.relevance import matches_query


@pytest.mark.parametrize('query,name,pack,expected', [
    ('popcorn', 'Crax Crunchy Pipes Masala Puffs Snacks', '70 g', False),
    ('popcorn', '4700BC Himalayan Salt Caramel Popcorn, Gourmet', '3 x 60 g', True),
    ('Diet Coke', 'Coca-Cola Diet Coke Can', '330 ml', True),
    ('Diet Coke', 'Coca-Cola Original', '330 ml', False),
    ('Diet Coke', 'Sprite Zero', '300 ml', False),
    ('milk', 'Britannia Milk Bikis Biscuits', '188 g', False),
    ('dahi', 'Nandini Curd', '500 g', True),
    ('Diet Cokes', 'Coca-Cola Diet Coke', '330 ml', True),
])
def test_query_relevance(query, name, pack, expected):
    assert matches_query(query, name, pack) is expected
