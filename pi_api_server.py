const express = require("express");
const mqtt = require("mqtt");
const cors = require("cors");

const app = express();
app.use(express.json());
app.use(cors());

const BROKER = "mqtt://3.67.46.166:1883";
const client = mqtt.connect(BROKER);

const pendingResponses = {};

client.on("connect", () => {
  console.log("✓ Connected to MQTT broker");
  client.subscribe("pi/+/response");
});

client.on("error", (err) => {
  console.error("✗ MQTT Error:", err.message);
});

client.on("message", (topic, message) => {
  const deviceId = topic.split("/")[1];
  
  let payload;
  try {
    payload = JSON.parse(message.toString());
  } catch (err) {
    payload = { success: false };
  }

  if (pendingResponses[deviceId]) {
    pendingResponses[deviceId](payload);
    delete pendingResponses[deviceId];
  }
});

app.post("/pi/:id/run", async (req, res) => {
  const deviceId = req.params.id;
  const { functionName, args } = req.body;
  const commandTopic = `pi/${deviceId}/command`;
  const payload = JSON.stringify({ functionName, args });

  try {
    const result = await new Promise((resolve, reject) => {
      pendingResponses[deviceId] = resolve;
      client.publish(commandTopic, payload);
      
      setTimeout(() => {
        if (pendingResponses[deviceId]) {
          delete pendingResponses[deviceId];
          reject("Timeout waiting for Pi response");
        }
      }, 180000); // 3 minutes
    });

    res.json(result);
  } catch (err) {
    res.status(500).json({ success: false, error: err.toString() });
  }
});

app.get("/health", (req, res) => {
  res.json({ success: true, message: "Central Server is running" });
});

const PORT = process.env.PORT || 8000;
app.listen(PORT, () => console.log(`Central Server running on ${PORT}`));