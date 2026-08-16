from rest_framework import serializers

from .models import Invoice, OrgUnit


class OrgUnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrgUnit
        fields = ["id", "name", "parent"]


class InvoiceSerializer(serializers.ModelSerializer):
    org_unit_name = serializers.CharField(source="org_unit.name", read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "number",
            "org_unit",
            "org_unit_name",
            "amount",
            "status",
            "created_by",
            "created_at",
        ]
        read_only_fields = ["created_by", "created_at"]