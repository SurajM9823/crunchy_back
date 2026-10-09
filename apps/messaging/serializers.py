from rest_framework import serializers

class SendSerializer(serializers.Serializer):
    client_id = serializers.UUIDField()
    text = serializers.CharField(max_length=2000, allow_blank=False, trim_whitespace=True)

class ReadSerializer(serializers.Serializer):
    last_message_id = serializers.IntegerField(min_value=0)
