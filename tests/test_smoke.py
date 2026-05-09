def test_package_imports() -> None:
    import remory

    assert callable(remory.main)
