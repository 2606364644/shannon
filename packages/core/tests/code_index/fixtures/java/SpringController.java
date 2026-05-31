package com.example.controller;

import org.springframework.web.bind.annotation.*;
import java.util.List;

@RestController
@RequestMapping("/api/users")
public class UserController {

    @GetMapping
    public List<Object> listUsers() {
        return userService.getUsers();
    }

    @PostMapping("/{id}")
    public Object updateUser(@PathVariable Long id, @RequestBody Object data) {
        return userService.saveUser(id, data);
    }

    @RabbitListener(queues = "orders")
    public void processOrder(String message) {
        orderService.handle(message);
    }
}
