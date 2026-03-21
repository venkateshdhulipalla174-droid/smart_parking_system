from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
import redis
import paho.mqtt.client as mqtt
import json
import time
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List
import psutil
import random

app = FastAPI(title="SmartPark AI", version="1.0.0")

# Redis connection
redis_client = redis.Redis(
    host='localhost', 
    port=6379, 
    decode_responses=True,
    socket_connect_timeout=5
)

# MQTT setup
mqtt_client = mqtt.Client()
mqtt_client.connect("broker.hivemq.com", 1883, 60)

class SensorData(BaseModel):
    sensor_id: str
    occupied: bool
    distance: float
    timestamp: int
    rssi: int

class BookingRequest(BaseModel):
    user_id: str
    mall_id: str = "phoenix_mall"
    duration_hours: int = 2
    vehicle_type: str = "regular"  # regular, ambulance, police, ev

class EmergencyRequest(BaseModel):
    vehicle_id: str
    vehicle_type: str  # ambulance, police, fire
    location_lat: float
    location_lng: float
    eta_minutes: int

class PredictionRequest(BaseModel):
    mall_id: str
    forecast_hours: int = 2

@app.post("/sensor/update")
async def update_slot(data: SensorData):
    """Receive sensor data from ESP32"""
    try:
        key = f"slot:{data.sensor_id}"
        redis_client.hset(key, mapping={
            "occupied": str(data.occupied),
            "distance": str(data.distance),
            "last_update": datetime.now().isoformat(),
            "rssi": str(data.rssi),
            "timestamp": str(data.timestamp)
        })
        
        # Publish real-time update
        redis_client.publish("slot_updates", json.dumps(data.dict()))
        
        # Update mall statistics
        update_mall_stats(data.sensor_id.split("_")[0])
        
        return {"status": "updated", "sensor": data.sensor_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/availability/{mall_id}")
async def get_availability(mall_id: str, forecast_hours: int = 0):
    """Get current availability with optional prediction"""
    try:
        slots = redis_client.keys(f"slot:{mall_id}_*")
        available = []
        occupied = []
        emergency_reserved = []
        
        for slot_key in slots:
            info = redis_client.hgetall(slot_key)
            slot_id = slot_key.split(":")[-1]
            
            slot_data = {
                "id": slot_id,
                "occupied": info.get("occupied") == "True",
                "last_update": info.get("last_update"),
                "reserved_for": info.get("reserved_for", None)
            }
            
            if info.get("reserved_for"):
                emergency_reserved.append(slot_data)
            elif slot_data["occupied"]:
                occupied.append(slot_data)
            else:
                available.append(slot_data)
        
        # Calculate occupancy rate
        total = len(slots)
        occupied_count = len(occupied) + len(emergency_reserved)
        occupancy_rate = (occupied_count / total * 100) if total > 0 else 0
        
        result = {
            "mall_id": mall_id,
            "total_slots": total,
            "available_count": len(available),
            "occupied_count": occupied_count,
            "emergency_reserved": len(emergency_reserved),
            "occupancy_rate": round(occupancy_rate, 1),
            "available_slots": available[:10],  # Limit for performance
            "timestamp": datetime.now().isoformat()
        }
        
        # Add prediction if requested
        if forecast_hours > 0:
            result["prediction"] = predict_occupancy(mall_id, forecast_hours)
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/book")
async def book_slot(request: BookingRequest):
    """Book a parking slot"""
    try:
        # Find best available slot
        available_slots = get_available_slots(request.mall_id)
        
        if not available_slots:
            raise HTTPException(status_code=400, detail="No slots available")
        
        # Select slot based on vehicle type
        if request.vehicle_type == "ev":
            selected = find_ev_slot(available_slots)
        else:
            selected = available_slots[0]
        
        # Reserve slot
        slot_key = f"slot:{request.mall_id}_{selected}"
        expiry = datetime.now() + timedelta(hours=request.duration_hours)
        
        redis_client.hset(slot_key, mapping={
            "occupied": "True",
            "reserved_by": request.user_id,
            "reserved_until": expiry.isoformat(),
            "vehicle_type": request.vehicle_type
        })
        
        # Calculate price
        price = calculate_price(request.mall_id, request.duration_hours, request.vehicle_type)
        
        return {
            "booking_id": f"BK{int(time.time())}",
            "slot_id": selected,
            "mall_id": request.mall_id,
            "reserved_until": expiry.isoformat(),
            "price": price,
            "qr_code": generate_qr(request.user_id, selected)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/emergency/priority")
async def emergency_priority(request: EmergencyRequest, background_tasks: BackgroundTasks):
    """Emergency vehicle priority booking"""
    start_time = time.time()
    
    try:
        # Find nearest available slot or force-free one
        slot_id = find_or_create_emergency_slot(request)
        
        if not slot_id:
            raise HTTPException(status_code=503, detail="No emergency slots available")
        
        # Reserve with priority
        slot_key = f"slot:phoenix_mall_{slot_id}"
        expiry = datetime.now() + timedelta(hours=1)
        
        redis_client.hset(slot_key, mapping={
            "occupied": "True",
            "reserved_for": request.vehicle_type,
            "vehicle_id": request.vehicle_id,
            "reserved_until": expiry.isoformat(),
            "emergency": "True",
            "eta_minutes": str(request.eta_minutes)
        })
        
        # Calculate response time
        response_time = time.time() - start_time
        
        # Send navigation instructions asynchronously
        background_tasks.add_task(
            send_navigation_instructions,
            request.vehicle_id,
            slot_id,
            request.location_lat,
            request.location_lng
        )
        
        return {
            "status": "priority_assigned",
            "vehicle_type": request.vehicle_type,
            "slot_id": slot_id,
            "response_time_seconds": round(response_time, 2),
            "navigation": {
                "entry_gate": "Gate A",
                "route": ["Entry", "Straight", "Level B", f"Slot {slot_id}"],
                "estimated_arrival": request.eta_minutes
            },
            "expires_at": expiry.isoformat()
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/price/{mall_id}")
async def get_price(mall_id: str, duration: int = 1):
    """Get current dynamic price"""
    return {
        "mall_id": mall_id,
        "duration_hours": duration,
        "base_rate": 40,
        "dynamic_multiplier": calculate_dynamic_multiplier(mall_id),
        "final_price": calculate_price(mall_id, duration, "regular"),
        "peak_hours": "14:00-20:00"
    }

@app.get("/health")
async def health_check():
    """Comprehensive health check"""
    checks = {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0",
        "services": {}
    }
    
    # Redis check
    try:
        redis_client.ping()
        checks["services"]["database"] = "connected"
    except:
        checks["services"]["database"] = "disconnected"
        checks["status"] = "degraded"
    
    # System resources
    checks["system"] = {
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_usage": psutil.disk_usage('/').percent
    }
    
    # Parking metrics
    try:
        all_slots = redis_client.keys("slot:*")
        checks["parking"] = {
            "total_sensors": len(all_slots),
            "active_sensors": len([s for s in all_slots if redis_client.hget(s, "last_update") and 
                                 (datetime.now() - datetime.fromisoformat(redis_client.hget(s, "last_update"))).seconds < 300]),
            "emergency_slots_active": len([s for s in all_slots if redis_client.hget(s, "emergency") == "True"])
        }
    except:
        checks["parking"] = "error"
    
    return checks

# Helper functions
def update_mall_stats(mall_id: str):
    """Update aggregate statistics"""
    pass

def get_available_slots(mall_id: str) -> List[str]:
    """Get list of available slot IDs"""
    slots = redis_client.keys(f"slot:{mall_id}_*")
    available = []
    for slot in slots:
        info = redis_client.hgetall(slot)
        if info.get("occupied") == "False" and not info.get("reserved_for"):
            available.append(slot.split("_")[-1])
    return available

def find_ev_slot(available_slots: List[str]) -> str:
    """Find EV charging slot"""
    # Simplified: return first available
    return available_slots[0] if available_slots else None

def find_or_create_emergency_slot(request: EmergencyRequest) -> Optional[str]:
    """Find or force-free slot for emergency"""
    available = get_available_slots("phoenix_mall")
    
    if available:
        return available[0]
    
    # Force-free nearest slot (simplified logic)
    occupied = redis_client.keys("slot:phoenix_mall_*")
    for slot in occupied:
        info = redis_client.hgetall(slot)
        if info.get("emergency") != "True":
            # Evict regular vehicle
            slot_id = slot.split("_")[-1]
            redis_client.hset(slot, "evicted", "True")
            return slot_id
    
    return None

def calculate_price(mall_id: str, hours: int, vehicle_type: str) -> float:
    """Calculate dynamic price"""
    base = 40
    multiplier = calculate_dynamic_multiplier(mall_id)
    
    if vehicle_type == "ev":
        base += 20  # EV charging premium
    elif vehicle_type in ["ambulance", "police"]:
        return 0  # Emergency vehicles free
    
    return round(base * multiplier * hours, 2)

def calculate_dynamic_multiplier(mall_id: str) -> float:
    """Calculate surge multiplier based on occupancy"""
    try:
        slots = redis_client.keys(f"slot:{mall_id}_*")
        if not slots:
            return 1.0
        
        occupied = sum(1 for s in slots if redis_client.hget(s, "occupied") == "True")
        rate = occupied / len(slots)
        
        if rate > 0.9:
            return 1.5
        elif rate > 0.7:
            return 1.2
        elif rate < 0.3:
            return 0.8
        return 1.0
    except:
        return 1.0

def predict_occupancy(mall_id: str, hours: int) -> dict:
    """Simple prediction (placeholder for ML model)"""
    # In production, call ML service
    current = calculate_dynamic_multiplier(mall_id)
    
    # Simulate prediction
    predicted_rate = min(95, 50 + hours * 10 + random.randint(-10, 10))
    
    return {
        "forecast_hours": hours,
        "predicted_occupancy_percent": predicted_rate,
        "confidence": "medium",
        "trend": "increasing" if predicted_rate > 60 else "stable",
        "recommendation": "Book now" if predicted_rate > 80 else "Available"
    }

def generate_qr(user_id: str, slot_id: str) -> str:
    """Generate QR code data"""
    return f"SMARTPARK:{user_id}:{slot_id}:{int(time.time())}"

def send_navigation_instructions(vehicle_id: str, slot_id: str, lat: float, lng: float):
    """Send navigation to vehicle"""
    time.sleep(1)  # Simulate async processing
    print(f"Navigation sent to {vehicle_id} for slot {slot_id}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
