from rest_framework import serializers

class SendSerializer(serializers.Serializer):
    client_id = serializers.UUIDField()
    text = serializers.CharField(max_length=2000, allow_blank=True, required=False, default="", trim_whitespace=True)

    file = serializers.FileField(required=False)
    def validate(self, data):
        if not data.get('text') and not data.get('file'):
            raise serializers.ValidationError('Write a message or choose a file.')
        return data

class ReadSerializer(serializers.Serializer):
    last_message_id = serializers.IntegerField(min_value=0)
