package main

import (
    "net/http"
    "encoding/json"
)

func listUsers(w http.ResponseWriter, r *http.Request) {
    users := getUsers()
    json.NewEncoder(w).Encode(users)
}

func updateUser(w http.ResponseWriter, r *http.Request) {
    var data map[string]interface{}
    json.NewDecoder(r.Body).Decode(&data)
    result := saveUser(data)
    json.NewEncoder(w).Encode(result)
}

func getUsers() []map[string]interface{} {
    return nil
}

func saveUser(data map[string]interface{}) map[string]interface{} {
    return data
}
