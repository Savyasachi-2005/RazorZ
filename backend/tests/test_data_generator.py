from app.data_generator import generate_dataset


def test_generated_dataset_has_order_and_payment_pairs():
    dataset = generate_dataset(records=50, seed=42)

    assert len(dataset) >= 50
    assert any(item.record_type == "order" for item in dataset)
    assert any(item.record_type == "payment" for item in dataset)
    assert any(item.record_type == "settlement" for item in dataset)
    assert any(item.record_type == "refund" for item in dataset)
    assert any(item.record_type == "fee" for item in dataset)
    assert all(item.amount >= 0 for item in dataset)
