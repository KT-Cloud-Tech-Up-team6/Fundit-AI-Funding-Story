from funding_story.tasks import LoadedReference, _image_output_size, _selected_references


def _reference(index, reward_id):
    return LoadedReference(
        slot_id=f"source-{index}",
        reward_id=reward_id,
        content=str(index).encode(),
        mime="image/png",
    )


def test_output_size_tracks_template_orientation():
    assert _image_output_size(1600, 900) == "1536x1024"
    assert _image_output_size(900, 1600) == "1024x1536"
    assert _image_output_size(1000, 1000) == "1024x1024"


def test_reward_references_are_prioritized_and_capped_at_sixteen():
    references = [_reference(0, None)]
    references += [_reference(index, 20) for index in range(1, 4)]
    references += [_reference(index, 10) for index in range(4, 24)]

    selected = _selected_references(references, 10)

    assert len(selected) == 16
    assert [content for content, _ in selected[:3]] == [b"4", b"5", b"6"]
    assert b"0" not in [content for content, _ in selected]


def test_general_slots_prioritize_project_references():
    references = [_reference(1, 10), _reference(2, None), _reference(3, 20)]
    selected = _selected_references(references, None)
    assert [content for content, _ in selected] == [b"2", b"1", b"3"]
