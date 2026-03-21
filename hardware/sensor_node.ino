
---

## File: `hardware/sensor_node.ino`

```cpp
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

const char* ssid = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_PASSWORD";
const char* mqtt_server = "broker.hivemq.com";
const int mqtt_port = 1883;

WiFiClient espClient;
PubSubClient client(espClient);

#define TRIG_PIN 5
#define ECHO_PIN 18
#define LED_PIN 2

String sensor_id = "A1";
unsigned long lastReconnectAttempt = 0;

void setup() {
  Serial.begin(115200);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  
  setup_wifi();
  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(callback);
}

void setup_wifi() {
  delay(10);
  Serial.println();
  Serial.print("Connecting to ");
  Serial.println(ssid);
  
  WiFi.begin(ssid, password);
  
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  
  Serial.println("");
  Serial.println("WiFi connected");
  Serial.println("IP address: ");
  Serial.println(WiFi.localIP());
}

void callback(char* topic, byte* payload, unsigned int length) {
  Serial.print("Message arrived [");
  Serial.print(topic);
  Serial.print("] ");
  
  for (int i = 0; i < length; i++) {
    Serial.print((char)payload[i]);
  }
  Serial.println();
}

boolean reconnect() {
  if (client.connect(("ESP32-" + sensor_id).c_str())) {
    Serial.println("Connected to MQTT");
    client.subscribe(("parking/command/" + sensor_id).c_str());
  }
  return client.connected();
}

float getDistance() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  
  long duration = pulseIn(ECHO_PIN, HIGH, 30000);
  if (duration == 0) return -1;
  
  float distance = duration * 0.034 / 2;
  return distance;
}

void loop() {
  if (!client.connected()) {
    unsigned long now = millis();
    if (now - lastReconnectAttempt > 5000) {
      lastReconnectAttempt = now;
      if (reconnect()) {
        lastReconnectAttempt = 0;
      }
    }
  } else {
    client.loop();
  }
  
  float distance = getDistance();
  bool occupied = (distance > 0 && distance < 50);
  
  StaticJsonDocument<256> doc;
  doc["sensor_id"] = sensor_id;
  doc["occupied"] = occupied;
  doc["distance"] = distance;
  doc["timestamp"] = millis();
  doc["rssi"] = WiFi.RSSI();
  
  char buffer[256];
  serializeJson(doc, buffer);
  
  client.publish("parking/status", buffer);
  
  digitalWrite(LED_PIN, occupied ? HIGH : LOW);
  
  Serial.print("Distance: ");
  Serial.print(distance);
  Serial.print(" cm | Occupied: ");
  Serial.println(occupied ? "YES" : "NO");
  
  delay(5000);
}
