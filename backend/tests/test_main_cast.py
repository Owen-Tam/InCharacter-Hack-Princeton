"""Who counts as a main character: ranked over the whole book."""

from companion.chat import pick_main


def test_gatsby_like_cast_keeps_the_leading_five():
    scores = {1: 1488, 2: 707, 3: 565, 4: 424, 5: 366, 6: 118, 7: 110, 8: 98, 9: 85, 10: 54}
    assert pick_main(scores) == {1, 2, 3, 4, 5}


def test_lottery_like_cast():
    scores = {1: 133, 2: 51, 3: 32, 4: 29, 5: 26, 6: 23, 7: 22, 8: 20, 9: 18}
    assert pick_main(scores) == {1, 2, 3, 4}


def test_at_least_three_when_one_character_dominates():
    assert pick_main({1: 500, 2: 40, 3: 30, 4: 10}) == {1, 2, 3}


def test_at_most_eight_in_a_flat_cast():
    scores = {i: 100 - i for i in range(1, 13)}
    assert pick_main(scores) == set(range(1, 9))


def test_characters_with_nothing_are_never_main():
    assert pick_main({1: 10, 2: 0, 3: 0}) == {1}
    assert pick_main({}) == set()
