package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"os"
	"regexp"

	"github.com/redis/go-redis/v9"
)

var ctx = context.Background()

func StartHealthCheckServer() {

	PORT := os.Getenv("HEALTH_CHECK_PORT")

	if PORT == "" {
		PORT = "8081"
	}

	http.HandleFunc("/healthcheck", healthCheckHandler)
	err := http.ListenAndServe(":"+PORT, nil)
	if errors.Is(err, http.ErrServerClosed) {
		fmt.Printf("server closed\n")
	} else if err != nil {
		fmt.Printf("error starting server: %s\n", err)
		os.Exit(1)
	}
}

func healthCheckHandler(w http.ResponseWriter, r *http.Request) {

	redisURL := fmt.Sprintf("redis://:%s@localhost:%s", os.Getenv("ADMIN_PASSWORD"), os.Getenv("NODE_PORT"))

	if os.Getenv("TLS") == "true" {
		redisURL = fmt.Sprintf("rediss://:%s@localhost:%s", os.Getenv("ADMIN_PASSWORD"), os.Getenv("NODE_PORT"))
	}

	options, err := redis.ParseURL(redisURL)

	if err != nil {
		fmt.Printf("error parsing redis url: %s\n", err)
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte("ERROR"))
		return
	}

	rdb := redis.NewClient(options)

	// Check if master
	dbInfo, err := rdb.Info(ctx).Result()

	if err != nil {
		fmt.Printf("error getting info: %s\n", err)
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte("ERROR"))
		return
	}

	roleRegex := regexp.MustCompile(`role:(\w+)`)
	role := roleRegex.FindStringSubmatch(dbInfo)

	if len(role) < 1 {
		fmt.Printf("role not found\n")
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte("ERROR"))
		return
	}

	if role[0] == "role:master" {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("OK"))
		return
	}

	if role[0] == "role:slave" {
		// Check if is synced with master
		masterSyncRegex := regexp.MustCompile(`master_sync_in_progress:(\d+)`)
		masterSync := masterSyncRegex.FindStringSubmatch(dbInfo)

		if len(masterSync) < 1 {
			fmt.Printf("master_sync_in_progress not found\n")
			w.WriteHeader(http.StatusInternalServerError)
			w.Write([]byte("ERROR"))
			return
		}

		if masterSync[1] == "0" {
			w.WriteHeader(http.StatusOK)
			w.Write([]byte("OK"))
			return
		}

		if masterSync[1] == "1" {
			fmt.Printf("Sync in progress\n")
			w.WriteHeader(http.StatusExpectationFailed)
			w.Write([]byte("ERROR"))
			return
		}
	}

	fmt.Printf("unknown role: %s\n", role)
	w.WriteHeader(http.StatusInternalServerError)
	w.Write([]byte("ERROR"))
	return
}

func main() {
	StartHealthCheckServer()
}
