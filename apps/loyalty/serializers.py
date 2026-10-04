from rest_framework import serializers


class TierInput(serializers.Serializer):
    name = serializers.CharField(max_length=80)
    threshold = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    percent = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100)


class ProgramInput(serializers.Serializer):
    enabled = serializers.BooleanField()
    version = serializers.IntegerField(min_value=1)
    tiers = TierInput(many=True, max_length=50)

    def validate_tiers(self, tiers):
        tiers = sorted(tiers, key=lambda t: t['threshold'])
        if len({t['threshold'] for t in tiers}) != len(tiers):
            raise serializers.ValidationError('Each spending threshold must be unique.')
        if any(b['percent'] < a['percent'] for a, b in zip(tiers, tiers[1:])):
            raise serializers.ValidationError('Higher spending tiers must not reduce the discount.')
        return [{k: str(v) for k, v in t.items()} for t in tiers]
