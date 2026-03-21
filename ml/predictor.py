import pandas as pd
import numpy as np
from prophet import Prophet
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error
import joblib
import json
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import redis

class ParkingPredictor:
    """
    Ensemble predictor combining Prophet for trend/seasonality
    and Random Forest for feature-based prediction
    """
    
    def __init__(self, model_path: str = None):
        self.prophet_model = None
        self.rf_model = None
        self.feature_columns = [
            'hour', 'day_of_week', 'is_weekend', 
            'is_holiday', 'temperature', 'rain_intensity',
            'nearby_events', 'traffic_congestion'
        ]
        self.redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
        
        if model_path:
            self.load_models(model_path)
    
    def prepare_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Extract time-based and contextual features"""
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df['hour'] = df['timestamp'].dt.hour
        df['day_of_week'] = df['timestamp'].dt.dayofweek
        df['is_weekend'] = (df['day_of_week'] >= 5).astype(int)
        df['is_holiday'] = df['timestamp'].apply(self.is_holiday).astype(int)
        df['month'] = df['timestamp'].dt.month
        
        # Cyclical encoding for hour
        df['hour_sin'] = np.sin(2 * np.pi * df['hour'] / 24)
        df['hour_cos'] = np.cos(2 * np.pi * df['hour'] / 24)
        
        return df
    
    def is_holiday(self, date: datetime) -> bool:
        """Check if date is a holiday (simplified)"""
        # In production, use python-holidays library
        holidays = ['2024-01-01', '2024-12-25']  # Example
        return date.strftime('%Y-%m-%d') in holidays
    
    def train(self, historical_data_path: str, mall_id: str = None):
        """
        Train both models on historical occupancy data
        
        Expected CSV format:
        timestamp,occupancy,temperature,rain_intensity,nearby_events,traffic_congestion
        """
        print(f"Loading data from {historical_data_path}")
        df = pd.read_csv(historical_data_path)
        df = self.prepare_features(df)
        
        # Train Prophet model (trend + seasonality)
        print("Training Prophet model...")
        prophet_df = df[['timestamp', 'occupancy']].rename(
            columns={'timestamp': 'ds', 'occupancy': 'y'}
        )
        
        self.prophet_model = Prophet(
            yearly_seasonality=True,
            weekly_seasonality=True,
            daily_seasonality=True,
            interval_width=0.8,
            changepoint_prior_scale=0.05
        )
        
        # Add custom seasonality for peak hours
        self.prophet_model.add_seasonality(
            name='peak_hours',
            period=1,
            fourier_order=3,
            condition_name='is_peak'
        )
        
        prophet_df['is_peak'] = prophet_df['ds'].dt.hour.isin([10, 11, 12, 13, 14, 18, 19, 20]).astype(int)
        self.prophet_model.fit(prophet_df)
        
        # Train Random Forest model (feature-based)
        print("Training Random Forest model...")
        feature_cols = self.feature_columns + ['hour_sin', 'hour_cos', 'month']
        
        X = df[feature_cols]
        y = df['occupancy']
        
        self.rf_model = RandomForestRegressor(
            n_estimators=100,
            max_depth=10,
            min_samples_split=5,
            random_state=42,
            n_jobs=-1
        )
        self.rf_model.fit(X, y)
        
        # Evaluate
        predictions = self.rf_model.predict(X)
        mae = mean_absolute_error(y, predictions)
        print(f"Random Forest MAE: {mae:.2f}%")
        
        # Save models
        self.save_models(mall_id)
        
        return {
            'prophet_trained': True,
            'rf_trained': True,
            'rf_mae': mae,
            'features_used': feature_cols
        }
    
    def predict(self, mall_id: str, target_time: datetime, 
                weather: Dict = None, events: Dict = None) -> Dict:
        """
        Generate ensemble prediction for specific time
        
        Args:
            mall_id: Mall identifier
            target_time: When to predict for
            weather: Dict with temperature, rain_intensity
            events: Dict with nearby_events, traffic_congestion
        """
        if not self.prophet_model or not self.rf_model:
            raise ValueError("Models not trained or loaded")
        
        # Prophet prediction
        future_df = pd.DataFrame({'ds': [target_time]})
        future_df['is_peak'] = int(target_time.hour in [10, 11, 12, 13, 14, 18, 19, 20])
        
        prophet_result = self.prophet_model.predict(future_df)
        prophet_pred = prophet_result['yhat'].values[0]
        prophet_lower = prophet_result['yhat_lower'].values[0]
        prophet_upper = prophet_result['yhat_upper'].values[0]
        
        # Random Forest prediction
        features = pd.DataFrame([{
            'hour': target_time.hour,
            'day_of_week': target_time.weekday(),
            'is_weekend': 1 if target_time.weekday() >= 5 else 0,
            'is_holiday': 1 if self.is_holiday(target_time) else 0,
            'temperature': weather.get('temperature', 25) if weather else 25,
            'rain_intensity': weather.get('rain', 0) if weather else 0,
            'nearby_events': events.get('events', 0) if events else 0,
            'traffic_congestion': events.get('traffic', 50) if events else 50,
            'hour_sin': np.sin(2 * np.pi * target_time.hour / 24),
            'hour_cos': np.cos(2 * np.pi * target_time.hour / 24),
            'month': target_time.month
        }])
        
        rf_pred = self.rf_model.predict(features)[0]
        
        # Ensemble: weighted average (Prophet for trend, RF for context)
        # Weight Prophet higher for longer forecasts, RF for short-term
        prophet_weight = 0.6
        rf_weight = 0.4
        
        ensemble_pred = (prophet_weight * prophet_pred) + (rf_weight * rf_pred)
        
        # Clamp to valid range
        ensemble_pred = max(0, min(100, ensemble_pred))
        
        # Calculate confidence based on agreement
        agreement = 100 - abs(prophet_pred - rf_pred)
        confidence = 'high' if agreement > 80 else 'medium' if agreement > 50 else 'low'
        
        # Store prediction in Redis for tracking
        self.store_prediction(mall_id, target_time, ensemble_pred)
        
        return {
            'mall_id': mall_id,
            'forecast_time': target_time.isoformat(),
            'predicted_occupancy_percent': round(ensemble_pred, 1),
            'confidence': confidence,
            'model_agreement_percent': round(agreement, 1),
            'components': {
                'prophet_prediction': round(prophet_pred, 1),
                'rf_prediction': round(rf_pred, 1),
                'prophet_interval': [round(prophet_lower, 1), round(prophet_upper, 1)]
            },
            'factors': {
                'is_peak_hour': future_df['is_peak'].values[0] == 1,
                'is_weekend': features['is_weekend'].values[0] == 1,
                'weather_impact': 'rain' if features['rain_intensity'].values[0] > 0 else 'clear'
            },
            'recommendation': self.generate_recommendation(ensemble_pred, confidence)
        }
    
    def generate_recommendation(self, prediction: float, confidence: str) -> str:
        """Generate user-friendly recommendation"""
        if prediction > 90:
            return "Critical: Book immediately or choose alternative"
        elif prediction > 75:
            return "High demand: Book now to secure spot"
        elif prediction > 50:
            return "Moderate: Monitor closely, book soon"
        else:
            return "Available: Plenty of spaces expected"
    
    def store_prediction(self, mall_id: str, target_time: datetime, prediction: float):
        """Store prediction for accuracy tracking"""
        key = f"prediction:{mall_id}:{target_time.strftime('%Y%m%d%H')}"
        self.redis_client.setex(key, 86400, str(prediction))  # 24h expiry
    
    def evaluate_accuracy(self, mall_id: str, hours: int = 24) -> Dict:
        """Compare recent predictions with actual occupancy"""
        # Implementation for model improvement
        return {'accuracy': 0, 'samples': 0}  # Placeholder
    
    def save_models(self, mall_id: str = None):
        """Save trained models to disk"""
        prefix = mall_id or 'general'
        joblib.dump(self.rf_model, f'models/{prefix}_rf_model.pkl')
        self.prophet_model.save(f'models/{prefix}_prophet_model.json')
        print(f"Models saved with prefix: {prefix}")
    
    def load_models(self, model_path: str):
        """Load trained models from disk"""
        import os
        if os.path.exists(f'{model_path}_rf_model.pkl'):
            self.rf_model = joblib.load(f'{model_path}_rf_model.pkl')
            self.prophet_model = Prophet()
            self.prophet_model = self.prophet_model.load(f'{model_path}_prophet_model.json')
            print(f"Models loaded from {model_path}")

# API endpoint for backend integration
def get_prediction(mall_id: str, hours_ahead: int = 2) -> Dict:
    """FastAPI-compatible prediction function"""
    predictor = ParkingPredictor(model_path='models/phoenix_mall')
    
    target = datetime.now() + timedelta(hours=hours_ahead)
    
    # Get real-time context from Redis or external APIs
    weather = {'temperature': 28, 'rain': 0}  # Placeholder
    events = {'events': 1, 'traffic': 65}     # Placeholder
    
    return predictor.predict(mall_id, target, weather, events)

if __name__ == "__main__":
    # Training example
    predictor = ParkingPredictor()
    
    # Create sample data for demo
    dates = pd.date_range(start='2024-01-01', end='2024-03-01', freq='H')
    sample_data = pd.DataFrame({
        'timestamp': dates,
        'occupancy': np.random.randint(20, 90, len(dates)),
        'temperature': np.random.randint(20, 35, len(dates)),
        'rain_intensity': np.random.choice([0, 0, 0, 5, 10], len(dates)),
        'nearby_events': np.random.choice([0, 0, 1], len(dates)),
        'traffic_congestion': np.random.randint(30, 80, len(dates))
    })
    sample_data.to_csv('data/training_data.csv', index=False)
    
    # Train
    result = predictor.train('data/training_data.csv', 'phoenix_mall')
    print("Training complete:", result)
    
    # Predict
    prediction = predictor.predict(
        'phoenix_mall',
        datetime.now() + timedelta(hours=3),
        weather={'temperature': 30, 'rain': 0},
        events={'events': 1, 'traffic': 70}
    )
    print("Prediction:", json.dumps(prediction, indent=2, default=str))
