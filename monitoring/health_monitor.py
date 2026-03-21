import time
import redis
import psutil
from datetime import datetime, timedelta
from prometheus_client import Counter, Gauge, Histogram, start_http_server, Info
import json
import threading

# Prometheus metrics
SENSOR_ONLINE = Gauge('parking_sensor_online', 'Sensor online status', ['sensor_id', 'mall_id'])
OCCUPANCY_RATE = Gauge('parking_occupancy_rate_percent', 'Current occupancy rate', ['mall_id'])
EMERGENCY_RESPONSE_TIME = Histogram('emergency_response_seconds', 'Emergency slot assignment time', 
                                    buckets=[.5, 1, 2, 5, 10, 30])
PREDICTION_ACCURACY = Gauge('ml_prediction_accuracy', 'Prediction vs actual accuracy', ['model_type', 'mall_id'])
ALERTS_TRIGGERED = Counter('parking_alerts_total', 'Total alerts by type', ['alert_type', 'severity'])
API_REQUESTS = Counter('api_requests_total', 'API requests by endpoint', ['method', 'endpoint', 'status'])
SYSTEM_CPU = Gauge('system_cpu_percent', 'System CPU usage')
SYSTEM_MEMORY = Gauge('system_memory_percent', 'System memory usage')
ACTIVE_BOOKINGS = Gauge('active_bookings', 'Current active bookings', ['mall_id'])
EMERGENCY_OVERRIDES = Counter('emergency_overrides_total', 'Regular slots freed for emergency')

APP_INFO = Info('smartpark', 'Application information')

class ParkingHealthMonitor:
    def __init__(self, redis_host='localhost', redis_port=6379):
        self.redis_client = redis.Redis(
            host=redis_host, 
            port=redis_port, 
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5
        )
        self.last_sensor_ping = {}
        self.running = False
        self.monitoring_thread = None
        
        # Set app info
        APP_INFO.info({'version': '1.0.0', 'environment': 'hackathon'})
        
    def start(self):
        """Start monitoring in background thread"""
        self.running = True
        self.monitoring_thread = threading.Thread(target=self._monitoring_loop)
        self.monitoring_thread.daemon = True
        self.monitoring_thread.start()
        
        # Start Prometheus HTTP server
        start_http_server(8001)
        print("Health monitor started on port 8001")
        
    def stop(self):
        self.running = False
        
    def _monitoring_loop(self):
        """Main monitoring loop"""
        while self.running:
            try:
                self.check_sensor_health()
                self.check_occupancy_anomaly()
                self.update_system_metrics()
                self.check_emergency_timeouts()
                self.update_booking_metrics()
                
                time.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                print(f"Monitor error: {e}")
                time.sleep(5)
    
    def check_sensor_health(self):
        """Check if sensors are reporting data"""
        try:
            sensors = self.redis_client.keys("slot:*")
            now = datetime.now()
            
            for sensor_key in sensors:
                parts = sensor_key.split(":")
                if len(parts) < 2:
                    continue
                    
                sensor_id = parts[-1]
                mall_id = parts[1] if len(parts) > 2 else "unknown"
                
                last_update = self.redis_client.hget(sensor_key, "last_update")
                
                if last_update:
                    try:
                        last_time = datetime.fromisoformat(last_update)
                        offline_threshold = now - timedelta(minutes=2)
                        
                        is_online = last_time > offline_threshold
                        SENSOR_ONLINE.labels(sensor_id=sensor_id, mall_id=mall_id).set(1 if is_online else 0)
                        
                        if not is_online:
                            ALERTS_TRIGGERED.labels(
                                alert_type='sensor_offline', 
                                severity='critical'
                            ).inc()
                            self.trigger_alert({
                                'type': 'sensor_offline',
                                'sensor_id': sensor_id,
                                'mall_id': mall_id,
                                'last_seen': last_update,
                                'action_required': 'Check sensor connectivity'
                            })
                            
                        self.last_sensor_ping[sensor_id] = last_time
                        
                    except ValueError:
                        # Invalid timestamp format
                        pass
                        
        except redis.RedisError as e:
            print(f"Redis error in sensor health check: {e}")
    
    def check_occupancy_anomaly(self):
        """Detect unusual occupancy patterns"""
        try:
            malls = ['phoenix_mall', 'm5_mall']  # Configurable
            
            for mall in malls:
                slots = self.redis_client.keys(f"slot:{mall}_*")
                if not slots:
                    continue
                
                occupied = 0
                for slot in slots:
                    if self.redis_client.hget(slot, "occupied") == "True":
                        occupied += 1
                
                rate = (occupied / len(slots)) * 100 if slots else 0
                OCCUPANCY_RATE.labels(mall_id=mall).set(rate)
                
                # Check for anomalies (simplified)
                # In production, compare with historical data
                if rate > 95:
                    ALERTS_TRIGGERED.labels(
                        alert_type='high_occupancy',
                        severity='warning'
                    ).inc()
                elif rate < 5 and self.is_peak_hour():
                    ALERTS_TRIGGERED.labels(
                        alert_type='sensor_malfunction_suspected',
                        severity='critical'
                    ).inc()
                    
        except Exception as e:
            print(f"Error in occupancy check: {e}")
    
    def check_emergency_timeouts(self):
        """Check for expired emergency reservations"""
        try:
            slots = self.redis_client.keys("slot:*")
            now = datetime.now()
            
            for slot in slots:
                reserved_until = self.redis_client.hget(slot, "reserved_until")
                is_emergency = self.redis_client.hget(slot, "emergency") == "True"
                
                if reserved_until and is_emergency:
                    try:
                        expiry = datetime.fromisoformat(reserved_until)
                        if now > expiry:
                            # Clear expired emergency reservation
                            self.redis_client.hdel(slot, "reserved_for", "emergency", "vehicle_id")
                            self.redis_client.hset(slot, "occupied", "False")
                            print(f"Cleared expired emergency reservation: {slot}")
                    except ValueError:
                        pass
                        
        except Exception as e:
            print(f"Error checking emergency timeouts: {e}")
    
    def update_system_metrics(self):
        """Update system resource metrics"""
        try:
            SYSTEM_CPU.set(psutil.cpu_percent(interval=0.1))
            SYSTEM_MEMORY.set(psutil.virtual_memory().percent)
        except Exception as e:
            print(f"Error updating system metrics: {e}")
    
    def update_booking_metrics(self):
        """Update booking-related metrics"""
        try:
            for mall in ['phoenix_mall', 'm5_mall']:
                slots = self.redis_client.keys(f"slot:{mall}_*")
                active = sum(1 for s in slots 
                           if self.redis_client.hget(s, "reserved_by") is not None)
                ACTIVE_BOOKINGS.labels(mall_id=mall).set(active)
        except Exception as e:
            print(f"Error updating booking metrics: {e}")
    
    def track_emergency_response(self, start_time: float):
        """Track emergency response time"""
        duration = time.time() - start_time
        EMERGENCY_RESPONSE_TIME.observe(duration)
        
        if duration > 10:
            ALERTS_TRIGGERED.labels(
                alert_type='slow_emergency_response',
                severity='critical'
            ).inc()
        
        return duration
    
    def track_prediction_accuracy(self, mall_id: str, model_type: str, 
                                   predicted: float, actual: float):
        """Track ML model accuracy"""
        accuracy = max(0, 100 - abs(predicted - actual))
        PREDICTION_ACCURACY.labels(model_type=model_type, mall_id=mall_id).set(accuracy)
        
        if accuracy < 70:
            ALERTS_TRIGGERED.labels(
                alert_type='low_prediction_accuracy',
                severity='warning'
            ).inc()
    
    def track_api_request(self, method: str, endpoint: str, status_code: int):
        """Track API request metrics"""
        status_class = f"{status_code // 100}xx"
        API_REQUESTS.labels(method=method, endpoint=endpoint, status=status_class).inc()
    
    def is_peak_hour(self) -> bool:
        """Check if current time is peak hour"""
        hour = datetime.now().hour
        return hour in [10, 11, 12, 13, 14, 18, 19, 20]
    
    def trigger_alert(self, alert_data: dict):
        """Send alert to admin dashboard"""
        try:
            self.redis_client.publish('admin_alerts', json.dumps(alert_data))
            self.redis_client.lpush('alert_history', json.dumps({
                **alert_data,
                'timestamp': datetime.now().isoformat()
            }))
            # Trim history to last 1000 alerts
            self.redis_client.ltrim('alert_history', 0, 999)
            
            print(f"ALERT: {alert_data['type']} - {alert_data.get('sensor_id', 'N/A')}")
        except Exception as e:
            print(f"Failed to send alert: {e}")
    
    def get_health_summary(self) -> dict:
        """Get current health status for API endpoint"""
        try:
            sensors = self.redis_client.keys("slot:*")
            online = 0
            offline = 0
            
            for sensor in sensors:
                last_update = self.redis_client.hget(sensor, "last_update")
                if last_update:
                    try:
                        last_time = datetime.fromisoformat(last_update)
                        if datetime.now() - last_time < timedelta(minutes=2):
                            online += 1
                        else:
                            offline += 1
                    except:
                        offline += 1
            
            return {
                'status': 'healthy' if offline == 0 else 'degraded',
                'timestamp': datetime.now().isoformat(),
                'sensors': {
                    'total': len(sensors),
                    'online': online,
                    'offline': offline
                },
                'system': {
                    'cpu_percent': psutil.cpu_percent(),
                    'memory_percent': psutil.virtual_memory().percent
                },
                'alerts_last_hour': self.redis_client.llen('alert_history')
            }
        except Exception as e:
            return {
                'status': 'unhealthy',
                'error': str(e)
            }

# Singleton instance for use in backend
_monitor_instance = None

def get_monitor() -> ParkingHealthMonitor:
    """Get or create monitor singleton"""
    global _monitor_instance
    if _monitor_instance is None:
        _monitor_instance = ParkingHealthMonitor()
    return _monitor_instance

if __name__ == "__main__":
    # Standalone monitoring
    monitor = ParkingHealthMonitor()
    monitor.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        monitor.stop()
        print("Monitor stopped")
