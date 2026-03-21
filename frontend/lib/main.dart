import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:google_maps_flutter/google_maps_flutter.dart';
import 'dart:convert';
import 'dart:async';

void main() => runApp(SmartParkApp());

class SmartParkApp extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'SmartPark AI',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        primarySwatch: Colors.blue,
        useMaterial3: true,
      ),
      home: HomeScreen(),
    );
  }
}

class HomeScreen extends StatefulWidget {
  @override
  _HomeScreenState createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int availableSlots = 0;
  int totalSlots = 0;
  double occupancyRate = 0;
  Map<String, dynamic>? prediction;
  bool isLoading = false;
  String selectedMall = 'phoenix_mall';
  Timer? refreshTimer;
  
  final String apiBaseUrl = 'http://your-api.com';
  final Map<String, LatLng> mallLocations = {
    'phoenix_mall': LatLng(19.0860, 72.8880),
    'm5_mall': LatLng(19.0760, 72.8777),
  };

  @override
  void initState() {
    super.initState();
    fetchAvailability();
    // Auto-refresh every 30 seconds
    refreshTimer = Timer.periodic(Duration(seconds: 30), (_) => fetchAvailability());
  }

  @override
  void dispose() {
    refreshTimer?.cancel();
    super.dispose();
  }

  Future<void> fetchAvailability({int forecastHours = 0}) async {
    setState(() => isLoading = true);
    try {
      final response = await http.get(
        Uri.parse('$apiBaseUrl/availability/$selectedMall?forecast_hours=$forecastHours'),
      ).timeout(Duration(seconds: 10));
      
      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        setState(() {
          availableSlots = data['available_count'];
          totalSlots = data['total_slots'];
          occupancyRate = data['occupancy_rate'];
          prediction = data['prediction'];
          isLoading = false;
        });
      }
    } catch (e) {
      setState(() => isLoading = false);
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Error: $e')),
      );
    }
  }

  Future<void> bookEmergencySlot(String vehicleType) async {
    setState(() => isLoading = true);
    try {
      final response = await http.post(
        Uri.parse('$apiBaseUrl/emergency/priority'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'vehicle_id': '${vehicleType}_${DateTime.now().millisecondsSinceEpoch}',
          'vehicle_type': vehicleType,
          'location_lat': mallLocations[selectedMall]!.latitude,
          'location_lng': mallLocations[selectedMall]!.longitude,
          'eta_minutes': 5,
        }),
      ).timeout(Duration(seconds: 15));

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        showDialog(
          context: context,
          builder: (_) => AlertDialog(
            title: Row(
              children: [
                Icon(Icons.emergency, color: Colors.red),
                SizedBox(width: 8),
                Text('Priority Assigned'),
              ],
            ),
            content: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Vehicle: ${data['vehicle_type'].toUpperCase()}', 
                     style: TextStyle(fontWeight: FontWeight.bold)),
                Text('Slot: ${data['slot_id']}'),
                Text('Response time: ${data['response_time_seconds']}s'),
                Divider(),
                Text('Navigation:', style: TextStyle(fontWeight: FontWeight.bold)),
                ...((data['navigation']['route'] as List).map((r) => 
                  Text('→ $r', style: TextStyle(fontSize: 12)))),
              ],
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context),
                child: Text('Start Navigation'),
              ),
            ],
          ),
        );
        fetchAvailability(); // Refresh
      }
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Emergency booking failed: $e')),
      );
    } finally {
      setState(() => isLoading = false);
    }
  }

  Future<void> bookRegularSlot() async {
    // Navigate to booking screen
    final result = await Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => BookingScreen(mallId: selectedMall)),
    );
    if (result == true) fetchAvailability();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('SmartPark AI'),
        actions: [
          IconButton(
            icon: Icon(Icons.refresh),
            onPressed: () => fetchAvailability(),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () => fetchAvailability(),
        child: SingleChildScrollView(
          physics: AlwaysScrollableScrollPhysics(),
          padding: EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Mall Selector
              DropdownButton<String>(
                value: selectedMall,
                isExpanded: true,
                items: mallLocations.keys.map((mall) => 
                  DropdownMenuItem(value: mall, child: Text(mall.replaceAll('_', ' ').toUpperCase()))
                ).toList(),
                onChanged: (val) {
                  setState(() => selectedMall = val!);
                  fetchAvailability();
                },
              ),
              SizedBox(height: 16),
              
              // Status Card
              Card(
                elevation: 4,
                child: Padding(
                  padding: EdgeInsets.all(16),
                  child: Column(
                    children: [
                      Row(
                        mainAxisAlignment: MainAxisAlignment.spaceBetween,
                        children: [
                          Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text('Available', style: TextStyle(color: Colors.grey)),
                              Text('$availableSlots/$totalSlots', 
                                   style: TextStyle(fontSize: 32, fontWeight: FontWeight.bold, color: Colors.green)),
                            ],
                          ),
                          CircularProgressIndicator(
                            value: occupancyRate / 100,
                            backgroundColor: Colors.grey[200],
                            valueColor: AlwaysStoppedAnimation(
                              occupancyRate > 80 ? Colors.red : occupancyRate > 50 ? Colors.orange : Colors.green,
                            ),
                          ),
                        ],
                      ),
                      SizedBox(height: 8),
                      LinearProgressIndicator(
                        value: occupancyRate / 100,
                        backgroundColor: Colors.grey[200],
                        valueColor: AlwaysStoppedAnimation(
                          occupancyRate > 80 ? Colors.red : occupancyRate > 50 ? Colors.orange : Colors.green,
                        ),
                      ),
                      SizedBox(height: 4),
                      Text('${occupancyRate.toStringAsFixed(1)}% occupied'),
                    ],
                  ),
                ),
              ),
              
              SizedBox(height: 16),
              
              // AI Prediction Card
              if (prediction != null) ...[
                Card(
                  color: Colors.blue[50],
                  child: Padding(
                    padding: EdgeInsets.all(16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Icon(Icons.psychology, color: Colors.blue),
                            SizedBox(width: 8),
                            Text('AI Prediction', style: TextStyle(fontWeight: FontWeight.bold)),
                          ],
                        ),
                        SizedBox(height: 8),
                        Text('In ${prediction!['forecast_hours']} hours:'),
                        Text('${prediction!['predicted_occupancy_percent']}% occupancy',
                             style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold)),
                        Text('Confidence: ${prediction!['confidence']}'),
                        Container(
                          padding: EdgeInsets.all(8),
                          margin: EdgeInsets.only(top: 8),
                          decoration: BoxDecoration(
                            color: prediction!['recommendation'].toString().contains('Critical') ? Colors.red[100] :
                                   prediction!['recommendation'].toString().contains('High') ? Colors.orange[100] : Colors.green[100],
                            borderRadius: BorderRadius.circular(8),
                          ),
                          child: Text(prediction!['recommendation'], 
                                      style: TextStyle(fontWeight: FontWeight.bold)),
                        ),
                      ],
                    ),
                  ),
                ),
                SizedBox(height: 16),
              ],
              
              // Action Buttons
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  ElevatedButton.icon(
                    onPressed: isLoading ? null : () => fetchAvailability(forecastHours: 2),
                    icon: Icon(Icons.timeline),
                    label: Text('Predict 2H'),
                  ),
                  ElevatedButton.icon(
                    onPressed: isLoading ? null : bookRegularSlot,
                    icon: Icon(Icons.book_online),
                    label: Text('Book Slot'),
                  ),
                ],
              ),
              
              SizedBox(height: 16),
              
              // Emergency Buttons
              Card(
                color: Colors.red[50],
                child: Padding(
                  padding: EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('EMERGENCY VEHICLES', 
                           style: TextStyle(color: Colors.red, fontWeight: FontWeight.bold)),
                      SizedBox(height: 8),
                      Row(
                        children: [
                          Expanded(
                            child: ElevatedButton.icon(
                              onPressed: isLoading ? null : () => bookEmergencySlot('ambulance'),
                              icon: Icon(Icons.medical_services, color: Colors.white),
                              label: Text('Ambulance'),
                              style: ElevatedButton.styleFrom(backgroundColor: Colors.red),
                            ),
                          ),
                          SizedBox(width: 8),
                          Expanded(
                            child: ElevatedButton.icon(
                              onPressed: isLoading ? null : () => bookEmergencySlot('police'),
                              icon: Icon(Icons.local_police, color: Colors.white),
                              label: Text('Police'),
                              style: ElevatedButton.styleFrom(backgroundColor: Colors.blue),
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ),
              
              SizedBox(height: 16),
              
              // Map Preview
              Container(
                height: 200,
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(12),
                  border: Border.all(color: Colors.grey),
                ),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(12),
                  child: GoogleMap(
                    initialCameraPosition: CameraPosition(
                      target: mallLocations[selectedMall]!,
                      zoom: 15,
                    ),
                    markers: {
                      Marker(
                        markerId: MarkerId(selectedMall),
                        position: mallLocations[selectedMall]!,
                        infoWindow: InfoWindow(
                          title: selectedMall.replaceAll('_', ' ').toUpperCase(),
                          snippet: '$availableSlots slots available',
                        ),
                      ),
                    },
                  ),
                ),
              ),
              
              if (isLoading) ...[
                SizedBox(height: 16),
                Center(child: CircularProgressIndicator()),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class BookingScreen extends StatefulWidget {
  final String mallId;
  BookingScreen({required this.mallId});

  @override
  _BookingScreenState createState() => _BookingScreenState();
}

class _BookingScreenState extends State<BookingScreen> {
  int duration = 2;
  String vehicleType = 'regular';
  bool isLoading = false;

  Future<void> confirmBooking() async {
    setState(() => isLoading = true);
    try {
      final response = await http.post(
        Uri.parse('http://your-api.com/book'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'user_id': 'user_${DateTime.now().millisecondsSinceEpoch}',
          'mall_id': widget.mallId,
          'duration_hours': duration,
          'vehicle_type': vehicleType,
        }),
      );

      if (response.statusCode == 200) {
        final data = jsonDecode(response.body);
        showDialog(
          context: context,
          barrierDismissible: false,
          builder: (_) => AlertDialog(
            title: Text('Booking Confirmed!'),
            content: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(Icons.check_circle, color: Colors.green, size: 64),
                Text('Slot: ${data['slot_id']}'),
                Text('Price: ₹${data['price']}'),
                Text('Valid until: ${data['reserved_until']}'),
                SizedBox(height: 16),
                Container(
                  padding: EdgeInsets.all(16),
                  color: Colors.grey[200],
                  child: Text('QR: ${data['qr_code']}', 
                             style: TextStyle(fontFamily: 'monospace', fontSize: 12)),
                ),
              ],
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context),
                child: Text('View in Maps'),
              ),
              ElevatedButton(
                onPressed: () {
                  Navigator.pop(context);
                  Navigator.pop(context, true);
                },
                child: Text('Done'),
              ),
            ],
          ),
        );
      }
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Booking failed: $e')),
      );
    } finally {
      setState(() => isLoading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text('Book Parking')),
      body: Padding(
        padding: EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Vehicle Type', style: TextStyle(fontWeight: FontWeight.bold)),
            Wrap(
              spacing: 8,
              children: ['regular', 'ev', 'disabled'].map((type) => ChoiceChip(
                label: Text(type.toUpperCase()),
                selected: vehicleType == type,
                onSelected: (_) => setState(() => vehicleType = type),
              )).toList(),
            ),
            SizedBox(height: 16),
            Text('Duration: $duration hours'),
            Slider(
              value: duration.toDouble(),
              min: 1,
              max: 8,
              divisions: 7,
              label: '$duration hours',
              onChanged: (val) => setState(() => duration = val.round()),
            ),
            Spacer(),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed: isLoading ? null : confirmBooking,
                child: isLoading 
                  ? CircularProgressIndicator(color: Colors.white)
                  : Text('Confirm Booking'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
