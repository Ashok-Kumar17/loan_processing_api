from django.db import models
from django.contrib.auth.models import User

class Loan(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='loans')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    mutual_fund_value = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, default='PENDING') # PENDING, APPROVED, REJECTED
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        username = self.user.username if self.user else "Unknown User"
        return f"Loan of {self.amount} for {self.user.username}"
    