from .models import Review


def parse_review(raw) -> Review:
    return Review.model_validate(raw)


def parse_initial_review(raw) -> Review:
    return Review.model_validate(raw)
