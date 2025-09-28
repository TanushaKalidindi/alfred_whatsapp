package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
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
    messages    map[string][]UnreadMessage // stores last 30 messages per chat
    processed   map[string]int            // tracks how many messages processed per chat
    outputURL   string                     // Python endpoint
    mongoCol    *mongo.Collection
    projectID   string
    mutex       sync.Mutex                // protect concurrent access to messages and processed maps
}

type UnreadSummary struct {
	TotalUnread   int             `json:"total_unread"`
	Messages      []UnreadMessage `json:"messages"`
	SyncTimestamp time.Time       `json:"sync_timestamp"`
}

var eventCollection *mongo.Collection

func main() {
	ctx := context.Background()

	projectID := os.Getenv("PROJECT_ID")
	if projectID == "" {
		projectID = "default-project" // Default project ID if not set
		fmt.Println("⚠️  PROJECT_ID environment variable not set, using default value")
	}

	fmt.Printf("🤖 Starting WhatsApp Agent with Project ID: %s...\n", projectID)

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
		client:    client,
		log:       clientLog,
		messages:  make(map[string][]UnreadMessage),
		processed: make(map[string]int),
		outputURL: "http://localhost:8000/process-whatsapp-messages",
		mongoCol:  eventCollection,
		projectID: projectID,
	}

	// Try to connect to MongoDB with better error handling
	mongoURI := "mongodb://alfred:alfred-coco-cola@172.178.91.142:27017/alfred-coco-cola?authSource=alfred-coco-cola"

	mongoClient, err := mongo.Connect(ctx, options.Client().ApplyURI(mongoURI))
	if err != nil {
		fmt.Printf("⚠️ MongoDB connection failed, continuing without it: %v\n", err)
		agent.mongoCol = nil
	} else {
		// Test the connection
		err = mongoClient.Ping(ctx, nil)
		if err != nil {
			fmt.Printf("⚠️ MongoDB ping failed, continuing without it: %v\n", err)
			agent.mongoCol = nil
		} else {
			eventCollection := mongoClient.Database("alfred-coco-cola").Collection("alfred_mongo_event_handlers")
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

	// Start HTTP server for trigger endpoint
	go func() {
		http.HandleFunc("/trigger-batch", func(w http.ResponseWriter, r *http.Request) {
			if r.Method != http.MethodPost {
				http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
				return
			}
			
			count := agent.sendBatchToPython()
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]interface{}{
				"status": "ok",
				"sent_messages": count,
			})
		})
		
		fmt.Println("🌐 Starting HTTP server on port 8081")
		if err := http.ListenAndServe(":8081", nil); err != nil {
			log.Fatalf("Failed to start HTTP server: %v", err)
		}
	}()

	// Wait for Ctrl+C
	c := make(chan os.Signal, 1)
	signal.Notify(c, os.Interrupt, syscall.SIGTERM)
	<-c

	fmt.Println("🛑 Shutting down...")
	client.Disconnect()
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
			"source":         "whatsapp",
			"raw_payload":    msg.Message,
			"to":        msg.To,
			"from":      msg.From,
			"created_at":     msg.Timestamp.UTC(),
			"project_id":   wa.projectID,
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

        // Filter for "Agent" group onlyZo 🌌🌍 Auroville 🌞 Zo
        if len(msgs) > 0 && (msgs[0].ChatName != "Mana inti sandesam🏘️" || msgs[0].ChatType != "group") {
            continue
        }

        // Get how many messages we've already processed for this chat
        processedCount := wa.processed[chatID]

        // Get unprocessed messages (new messages)
        if processedCount >= len(msgs) {
            continue // No new messages
        }

        unprocessedMsgs := msgs[processedCount:]
        if len(unprocessedMsgs) == 0 {
            continue
        }

        // Get context: 10 messages before the first unprocessed message
        contextMsgs := []UnreadMessage{}
        contextStart := processedCount - 10
        if contextStart < 0 {
            contextStart = 0
        }
        if processedCount > 0 {
            contextMsgs = msgs[contextStart:processedCount]
        }

        // Create a single batch entry with all unprocessed messages and context
        if len(unprocessedMsgs) > 0 {
            batch = append(batch, map[string]interface{}{
                "chat_id":   chatID,
                "chat_name": unprocessedMsgs[0].ChatName,
                "chat_type": unprocessedMsgs[0].ChatType,
                "context":   append(contextMsgs, unprocessedMsgs...),
                "timestamp": unprocessedMsgs[len(unprocessedMsgs)-1].Timestamp,
            })
        }

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
    
    payload, _ := json.Marshal(map[string]interface{}{
        "messages": batch,
    })

    resp, err := http.Post(wa.outputURL, "application/json", bytes.NewBuffer(payload))
    if err != nil {
        fmt.Printf("❌ Failed to send batch: %v\n", err)
        return 0
    }
    defer resp.Body.Close()
    fmt.Printf("✅ Sent batch to Python, status: %s\n", resp.Status)
    
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