package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"io"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/mongo"
	"go.mongodb.org/mongo-driver/mongo/options"

	"github.com/mdp/qrterminal/v3"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	_ "github.com/mattn/go-sqlite3"
)

// Config holds all configuration values
type Config struct {
	ProjectID         string
	MongoURI          string
	MongoDatabase     string
	MongoCollection   string
	BatchInterval     time.Duration
	PythonEndpoint    string
	TargetGroupName   string
	HTTPServerPort    string
}

type UnreadMessage struct {
	From      string    `json:"from"`
	To        string    `json:"to"`
	ChatName  string    `json:"chat_name"`
	ChatType  string    `json:"chat_type"`
	JID       string    `json:"message_id"`
	Timestamp time.Time `json:"timestamp"`
	Message   string    `json:"message"`
}

type WhatsAppAgent struct {
	client      *whatsmeow.Client
	log         waLog.Logger
	messages    map[string][]UnreadMessage
	processed   map[string]int
	mongoCol    *mongo.Collection
	mutex       sync.Mutex
	batchTicker *time.Ticker
	config      *Config
}

type UnreadSummary struct {
	TotalUnread   int             `json:"total_unread"`
	Messages      []UnreadMessage `json:"messages"`
	SyncTimestamp time.Time       `json:"sync_timestamp"`
}

var eventCollection *mongo.Collection

// loadConfig loads configuration from environment variables with defaults
func loadConfig() *Config {
	// Build MongoDB Atlas URI just like your Python get_database_uri()
	username := getEnv("MONGODB_USERNAME", "alfreddeveloper_db_user")
	password := getEnv("MONGODB_PASSWORD", "sBqjBA-n.5NX-qb")
	cluster := getEnv("MONGODB_CLUSTER", "purelight.dcqqgb8.mongodb.net")
	database := getEnv("MONGODB_DATABASE", "purelight")

	mongoURI := "mongodb+srv://" + username + ":" + password +
		"@" + cluster + "/" + database +
		"?retryWrites=true&w=majority&appName=purelight"

	config := &Config{
		ProjectID:       getEnv("PROJECT_ID", "68fa0c2d27a9893d3fc91c1b"),
		MongoURI:        mongoURI,
		MongoDatabase:   database,
		MongoCollection: getEnv("MONGO_COLLECTION", "whatsapp_event_log"),
		PythonEndpoint:  getEnv("PYTHON_ENDPOINT", "http://localhost:8003/process-whatsapp-messages"),
		TargetGroupName: getEnv("TARGET_GROUP_NAME", "WG 142MW MH ParaBond"),
		HTTPServerPort:  getEnv("HTTP_SERVER_PORT", "8082"),
	}

	// Parse batch interval (in minutes)
	batchIntervalMinutes := getEnvAsInt("BATCH_INTERVAL_MINUTES", 1)
	config.BatchInterval = time.Duration(batchIntervalMinutes) * time.Minute

	return config
}

// getEnv gets an environment variable or returns a default value
func getEnv(key, defaultValue string) string {
	value := os.Getenv(key)
	if value == "" {
		return defaultValue
	}
	return value
}

// getEnvAsInt gets an environment variable as int or returns a default value
func getEnvAsInt(key string, defaultValue int) int {
	valueStr := os.Getenv(key)
	if valueStr == "" {
		return defaultValue
	}
	var value int
	_, err := fmt.Sscanf(valueStr, "%d", &value)
	if err != nil {
		return defaultValue
	}
	return value
}

func main() {
	ctx := context.Background()

	// Load configuration
	config := loadConfig()

	fmt.Println("🔧 Configuration loaded:")
	fmt.Printf("   📁 Project ID: %s\n", config.ProjectID)
	fmt.Printf("   🗄️  MongoDB URI: %s\n", maskMongoURI(config.MongoURI))
	fmt.Printf("   📊 Database: %s\n", config.MongoDatabase)
	fmt.Printf("   📦 Collection: %s\n", config.MongoCollection)
	fmt.Printf("   ⏰ Batch Interval: %v\n", config.BatchInterval)
	fmt.Printf("   🐍 Python Endpoint: %s\n", config.PythonEndpoint)
	fmt.Printf("   👥 Target Group: %s\n", config.TargetGroupName)
	fmt.Printf("   🌐 HTTP Port: %s\n", config.HTTPServerPort)

	fmt.Printf("\n🤖 Starting WhatsApp Agent...\n")

	// SQLite storage
	dbLog := waLog.Stdout("Database", "ERROR", true)
	container, err := sqlstore.New(ctx, "sqlite3", "file:whatsapp.db?_foreign_keys=on", dbLog)
	if err != nil {
		log.Fatalf("Failed to create SQLite store: %v", err)
	}

	device, err := container.GetFirstDevice(ctx)
	if err != nil {
		log.Fatalf("Failed to get device: %v", err)
	}

	clientLog := waLog.Stdout("Client", "ERROR", true)
	client := whatsmeow.NewClient(device, clientLog)

	agent := &WhatsAppAgent{
		client:      client,
		log:         clientLog,
		messages:    make(map[string][]UnreadMessage),
		processed:   make(map[string]int),
		mongoCol:    eventCollection,
		batchTicker: time.NewTicker(config.BatchInterval),
		config:      config,
	}

	// Try to connect to MongoDB
	mongoClient, err := mongo.Connect(ctx, options.Client().ApplyURI(config.MongoURI))
	if err != nil {
		fmt.Printf("⚠️ MongoDB connection failed, continuing without it: %v\n", err)
		agent.mongoCol = nil
	} else {
		err = mongoClient.Ping(ctx, nil)
		if err != nil {
			fmt.Printf("⚠️ MongoDB ping failed, continuing without it: %v\n", err)
			agent.mongoCol = nil
		} else {
			eventCollection := mongoClient.Database(config.MongoDatabase).Collection(config.MongoCollection)
			agent.mongoCol = eventCollection
			fmt.Println("✅ MongoDB connected and collection set")
		}
	}

	client.AddEventHandler(agent.eventHandler)

	// Login / QR Code
	if client.Store.ID == nil {
		fmt.Println("🔑 Not logged in. Scan QR code to login:")
		qrChan, _ := client.GetQRChannel(ctx)
		err = client.Connect()
		if err != nil {
			log.Fatalf("Failed to connect: %v", err)
		}

		for evt := range qrChan {
			if evt.Event == "code" {
				qrterminal.GenerateHalfBlock(evt.Code, qrterminal.L, os.Stdout)
				fmt.Println("👆 Scan QR code in WhatsApp > Linked Devices > Link a Device")
			} else if evt.Event == "success" {
				fmt.Println("✅ Successfully logged in!")
				break
			}
		}
	} else {
		fmt.Printf("🔄 Already logged in as: %s\n", client.Store.ID.String())
		err = client.Connect()
		if err != nil {
			log.Fatalf("Failed to connect: %v", err)
		}
	}

	fmt.Println("⏳ Waiting for WhatsApp to sync...")
	time.Sleep(5 * time.Second)
	fmt.Println("👂 Listening for new messages...")
	fmt.Printf("⏰ Automatic batch processing every %v enabled\n", config.BatchInterval)

	// Start automatic batch processing goroutine
	go agent.startAutoBatchProcessing()

	// Start HTTP server for manual trigger endpoint
	go func() {
		http.HandleFunc("/trigger-batch", func(w http.ResponseWriter, r *http.Request) {
			if r.Method != http.MethodPost {
				http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
				return
			}

			count := agent.sendBatchToPython()
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]interface{}{
				"status":        "ok",
				"sent_messages": count,
			})
		})

		http.HandleFunc("/config", func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]interface{}{
				"project_id":       config.ProjectID,
				"mongo_database":   config.MongoDatabase,
				"mongo_collection": config.MongoCollection,
				"batch_interval":   config.BatchInterval.String(),
				"target_group":     config.TargetGroupName,
				"python_endpoint":  config.PythonEndpoint,
			})
		})

		fmt.Printf("🌐 Starting HTTP server on port %s\n", config.HTTPServerPort)
		if err := http.ListenAndServe(":"+config.HTTPServerPort, nil); err != nil {
			log.Fatalf("Failed to start HTTP server: %v", err)
		}
	}()

	// Wait for Ctrl+C
	c := make(chan os.Signal, 1)
	signal.Notify(c, os.Interrupt, syscall.SIGTERM)
	<-c

	fmt.Println("🛑 Shutting down...")
	agent.batchTicker.Stop()
	client.Disconnect()
}

// maskMongoURI masks sensitive information in MongoDB URI
func maskMongoURI(uri string) string {
	if len(uri) < 20 {
		return "***"
	}
	// Simple masking - show protocol and last part
	return uri[:10] + "***" + uri[len(uri)-20:]
}

// startAutoBatchProcessing runs in a goroutine and sends batches at configured intervals
func (wa *WhatsAppAgent) startAutoBatchProcessing() {
	for range wa.batchTicker.C {
		fmt.Println("⏰ Auto-batch trigger: Processing messages...")
		count := wa.sendBatchToPython()
		if count > 0 {
			fmt.Printf("✅ Auto-batch sent %d message groups to Python\n", count)
		} else {
			fmt.Println("ℹ️ Auto-batch: No new messages to process")
		}
	}
}

func (wa *WhatsAppAgent) eventHandler(evt interface{}) {
	switch v := evt.(type) {
	case *events.Message:
		wa.handleNewMessage(v)
	case *events.Connected:
		fmt.Println("✅ Connected to WhatsApp!")
		if wa.client.Store.ID != nil {
			fmt.Printf("📱 Logged in as: %s\n", wa.client.Store.ID.User)
		}
	case *events.Disconnected:
		fmt.Println("❌ Disconnected from WhatsApp")
	case *events.AppStateSyncComplete:
		fmt.Println("📱 App state sync complete")
	}
}

func (wa *WhatsAppAgent) handleNewMessage(evt *events.Message) {
	messageText := ""
	if text := evt.Message.GetConversation(); text != "" {
		messageText = text
	} else if ext := evt.Message.GetExtendedTextMessage(); ext != nil {
		messageText = ext.GetText()
	} else if img := evt.Message.GetImageMessage(); img != nil {
		messageText = "[Image]"
		if caption := img.GetCaption(); caption != "" {
			messageText = "[Image: " + caption + "]"
		}
	} else if doc := evt.Message.GetDocumentMessage(); doc != nil {
		messageText = "[Document: " + doc.GetFileName() + "]"
	} else if audio := evt.Message.GetAudioMessage(); audio != nil {
		messageText = "[Audio]"
	} else if video := evt.Message.GetVideoMessage(); video != nil {
		messageText = "[Video]"
		if caption := video.GetCaption(); caption != "" {
			messageText = "[Video: " + caption + "]"
		}
	} else {
		messageText = "[Other message type]"
	}

	jid := evt.Info.ID
	chatName := wa.getChatName(context.Background(), evt.Info.Chat)
	chatType := "individual"
	if evt.Info.IsGroup {
		chatType = "group"
	}

	msg := UnreadMessage{
		From:      evt.Info.Sender.String(),
		To:        evt.Info.Chat.String(),
		ChatName:  chatName,
		ChatType:  chatType,
		JID:       jid,
		Timestamp: evt.Info.Timestamp,
		Message:   messageText,
	}

	wa.addMessage(msg)

	// Print JSON immediately for Python to capture
	jsonData, _ := json.Marshal(map[string]interface{}{
		"type":      "new_message",
		"chat_jid":  evt.Info.Chat.String(),
		"chat_name": chatName,
		"chat_type": chatType,
		"from":      evt.Info.Sender.String(),
		"to":        evt.Info.Chat.String(),
		"message":   messageText,
		"time":      evt.Info.Timestamp.Format(time.RFC3339),
	})
	fmt.Println(string(jsonData))

	// Only insert into MongoDB if connection is available
	if wa.mongoCol != nil {
		payload := bson.M{
			"source":      "whatsapp",
			"raw_payload": msg.Message,
			"to":          msg.To,
			"from":        msg.From,
			"timestamp":   msg.Timestamp.UTC(),
			"project_id":  wa.config.ProjectID,
		}

		_, err := wa.mongoCol.InsertOne(context.Background(), payload)
		if err != nil {
			fmt.Printf("❌ Failed to insert message into MongoDB: %v\n", err)
		} else {
			fmt.Println("💾 Inserted message into MongoDB")
		}
	} else {
		fmt.Println("⚠️ MongoDB not available, skipping database insert")
	}
}

func (wa *WhatsAppAgent) prepareBatch() []map[string]interface{} {
    batch := []map[string]interface{}{}

    for chatID, msgs := range wa.messages {
        if len(msgs) == 0 {
            continue
        }

        // Filter for target group only
        if len(msgs) > 0 && (msgs[0].ChatName != wa.config.TargetGroupName || msgs[0].ChatType != "group") {
            continue
        }

        processedCount := wa.processed[chatID]

        // Skip if no new messages
        if processedCount >= len(msgs) {
            continue
        }

        unprocessedMsgs := msgs[processedCount:]
        if len(unprocessedMsgs) == 0 {
            continue
        }

        // Get up to 10 previous messages as context
        // Get up to 10 previous messages as context (better handling for first batch)
		contextMsgs := []UnreadMessage{}

		if len(msgs) > 0 {
			end := processedCount
			if end == 0 {
				// On first batch, use all messages except the newest ones
				end = len(msgs) - len(unprocessedMsgs)
				if end < 0 {
					end = 0
				}
			}

			contextStart := end - 10
			if contextStart < 0 {
				contextStart = 0
			}

			if end > contextStart {
				contextMsgs = msgs[contextStart:end]
			}
		}


        // Convert messages to the format expected by the Python endpoint
        contextMessages := []map[string]interface{}{}  // Initialize as empty slice
        for _, msg := range contextMsgs {
            contextMessages = append(contextMessages, map[string]interface{}{
                "from":       msg.From,
                "to":         msg.To,
                "chat_name":  msg.ChatName,
                "chat_type":  msg.ChatType,
                "message_id": msg.JID,
                "timestamp":  msg.Timestamp.Format(time.RFC3339),
                "message":    msg.Message,
            })
        }

        var newMessages []map[string]interface{}
        for _, msg := range unprocessedMsgs {
            newMessages = append(newMessages, map[string]interface{}{
                "from":       msg.From,
                "to":         msg.To,
                "chat_name":  msg.ChatName,
                "chat_type":  msg.ChatType,
                "message_id": msg.JID,
                "timestamp":  msg.Timestamp.Format(time.RFC3339),
                "message":    msg.Message,
            })
        }

        // Add the batch with properly formatted messages
        batch = append(batch, map[string]interface{}{
            "chat_id":         chatID,
            "chat_name":       unprocessedMsgs[0].ChatName,
            "chat_type":       unprocessedMsgs[0].ChatType,
            "context_messages": contextMessages,
            "new_messages":    newMessages,
            "timestamp":       unprocessedMsgs[len(unprocessedMsgs)-1].Timestamp.Format(time.RFC3339),
        })

        // Update processed count
        wa.processed[chatID] = len(msgs)
    }

    return batch
}

func (wa *WhatsAppAgent) sendBatchToPython() int {
    wa.mutex.Lock()
    batch := wa.prepareBatch()
    wa.mutex.Unlock()

    if len(batch) == 0 {
        fmt.Println("ℹ️ No new messages to send")
        return 0
    }

    // Log the payload for debugging
    payload, err := json.Marshal(map[string]interface{}{
        "messages": batch,
    })
    if err != nil {
        fmt.Printf("❌ Failed to marshal batch: %v\n", err)
        return 0
    }

    fmt.Printf("📤 Sending batch to Python endpoint (%d bytes)\n", len(payload))
    resp, err := http.Post(wa.config.PythonEndpoint, "application/json", bytes.NewBuffer(payload))
    if err != nil {
        fmt.Printf("❌ Failed to send batch: %v\n", err)
        return 0
    }
    defer resp.Body.Close()

    body, _ := io.ReadAll(resp.Body)
    if resp.StatusCode != http.StatusOK {
        fmt.Printf("❌ Error from Python endpoint (%d): %s\n", resp.StatusCode, string(body))
        return 0
    }

    fmt.Printf("✅ Successfully sent batch (%d messages)\n", len(batch))
    return len(batch)
}

func (wa *WhatsAppAgent) addMessage(msg UnreadMessage) {
	wa.mutex.Lock()
	defer wa.mutex.Unlock()
	chatID := msg.To

	if _, exists := wa.messages[chatID]; !exists {
		wa.messages[chatID] = []UnreadMessage{}
	}

	wa.messages[chatID] = append(wa.messages[chatID], msg)

	if len(wa.messages[chatID]) > 30 {
		wa.messages[chatID] = wa.messages[chatID][len(wa.messages[chatID])-30:]
	}
}

func (wa *WhatsAppAgent) getChatName(ctx context.Context, jid types.JID) string {
	if jid.Server == types.GroupServer {
		groupInfo, err := wa.client.GetGroupInfo(jid)
		if err == nil && groupInfo.Name != "" {
			return groupInfo.Name
		}
		return fmt.Sprintf("Group (%s)", jid.User[:8]+"...")
	}

	contact, err := wa.client.Store.Contacts.GetContact(ctx, jid)
	if err == nil && contact.PushName != "" {
		return contact.PushName
	}
	return jid.User
}