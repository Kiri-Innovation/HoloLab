import unittest

import torch
from silhouette_loss import silhouette_loss


class SilhouetteLossTest(unittest.TestCase):
    def test_background_clears_and_foreground_fills(self):
        alpha = torch.tensor([[[0.99, 0.01]]], requires_grad=True)
        loss = silhouette_loss(alpha, torch.tensor([[[0, 1]]], dtype=torch.uint8))
        loss.backward()
        self.assertGreater(float(alpha.grad[0, 0, 0]), 0)
        self.assertLess(float(alpha.grad[0, 0, 1]), 0)
        self.assertTrue(torch.isfinite(alpha.grad).all())

    def test_exact_binary_prediction_is_finite(self):
        alpha = torch.tensor([[[0.0, 1.0]]], requires_grad=True)
        loss = silhouette_loss(alpha, torch.tensor([[[0, 1]]], dtype=torch.uint8))
        loss.backward()
        self.assertLess(float(loss.detach()), 2e-5)
        self.assertTrue(torch.isfinite(alpha.grad).all())

    def test_mismatched_shapes_fail(self):
        with self.assertRaisesRegex(ValueError, "matching shapes"):
            silhouette_loss(torch.zeros(3, 3), torch.zeros(4, 4))


if __name__ == "__main__":
    unittest.main()
