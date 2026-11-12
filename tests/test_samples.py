from vera_in_viewer.samples import SAMPLES_DIR, list_samples, sample_files


def test_samples_index_matches_files():
    index = list_samples()
    assert [s["name"] for s in index] == [f.name for f in sample_files()]
    for sample in index:
        assert sample["title"]
        assert sample["info"]
        assert (SAMPLES_DIR / sample["preview"]).is_file()
